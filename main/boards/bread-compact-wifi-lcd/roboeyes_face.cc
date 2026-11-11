#include "roboeyes_face.h"

#include <math.h>
#include <string.h>

#if defined(ROBOEYES_FACE_HOST)
#include <stdio.h>
#include <stdlib.h>
#endif

#if defined(ROBOEYES_FACE_HOST) || CONFIG_LCD_GC9A01_240X240

namespace {

constexpr float kScreenCx = 119.5f;
constexpr float kScreenCy = 119.5f;
constexpr float kScreenR = 120.5f;

constexpr int kBlinkEndMs = 560;
constexpr float kEyeSpacing = 92.0f;

// Idle open-eye disk measured from the 240x240 reference preview:
// left center (72.5, 118.5), diameter 69, RGB(120, 230, 255).
constexpr float kIdleCx = 72.5f;
constexpr float kIdleCy = 118.5f;
constexpr float kIdleR = 34.5f;

// Happy crescent: same family of disk, with a lower disk subtracted.
// Fitted to the reference happy frame (outer IoU about 0.97).
constexpr float kHappyCx = 73.0f;
constexpr float kHappyCy = 119.5f;
constexpr float kHappyR = 34.7f;
constexpr float kHappyCutDy = 38.5f;
constexpr float kHappyCutR = 36.5f;

constexpr uint8_t kEyeR = 120;
constexpr uint8_t kEyeG = 230;
constexpr uint8_t kEyeB = 255;

float Clampf(float value, float lo, float hi) {
    if (value < lo) {
        return lo;
    }
    if (value > hi) {
        return hi;
    }
    return value;
}

uint32_t NextRand(RoboEyesFace* face) {
    uint32_t x = face->rng;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    if (x == 0) {
        x = 0x6D2B79F5u;
    }
    face->rng = x;
    return x;
}

float RandRange(RoboEyesFace* face, float lo, float hi) {
    float unit = static_cast<float>(NextRand(face) >> 8) * (1.0f / 16777216.0f);
    return lo + (hi - lo) * unit;
}

float BlinkOpen(int t_ms) {
    // Heights taken from the reference blink (80 ms steps): 69, 18, 5, 2, 35, 61, 67, 69.
    static const int kTimes[] = {0, 80, 160, 240, 320, 400, 480, 560};
    static const float kOpens[] = {1.0f,          18.0f / 69.0f, 5.0f / 69.0f,  2.0f / 69.0f,
                                   35.0f / 69.0f, 61.0f / 69.0f, 67.0f / 69.0f, 1.0f};
    if (t_ms <= 0) {
        return 1.0f;
    }
    if (t_ms >= kBlinkEndMs) {
        return 1.0f;
    }
    for (int i = 0; i < 7; ++i) {
        if (t_ms <= kTimes[i + 1]) {
            float u = static_cast<float>(t_ms - kTimes[i]) /
                      static_cast<float>(kTimes[i + 1] - kTimes[i]);
            return kOpens[i] + (kOpens[i + 1] - kOpens[i]) * u;
        }
    }
    return 1.0f;
}

float CircleCov(float x, float y, float cx, float cy, float r) {
    float dx = x - cx;
    float dy = y - cy;
    float dist2 = dx * dx + dy * dy;
    float inner = r - 0.75f;
    if (inner < 0.5f) {
        inner = 0.5f;
    }
    float outer = r + 0.75f;
    if (dist2 <= inner * inner) {
        return 1.0f;
    }
    if (dist2 >= outer * outer) {
        return 0.0f;
    }
    float dist = sqrtf(dist2);
    return Clampf((outer - dist) / (outer - inner), 0.0f, 1.0f);
}

float EllipseCov(float x, float y, float cx, float cy, float rx, float ry) {
    if (rx < 0.4f || ry < 0.4f) {
        return 0.0f;
    }
    float nx = (x - cx) / rx;
    float ny = (y - cy) / ry;
    float dist2 = nx * nx + ny * ny;
    float aa = 1.15f / (rx < ry ? rx : ry);
    float inner = 1.0f - aa;
    float outer = 1.0f + aa;
    if (dist2 <= inner * inner) {
        return 1.0f;
    }
    if (dist2 >= outer * outer) {
        return 0.0f;
    }
    float dist = sqrtf(dist2);
    return Clampf((outer - dist) / (outer - inner), 0.0f, 1.0f);
}

struct EyeGeom {
    float cx;
    float cy;
    float r;
    float cut_r;
    float cut_dy;
};

EyeGeom EyeGeometry(RoboEyesMood mood, int index, float gaze_x, float gaze_y, float bounce) {
    EyeGeom eye;
    eye.cx = kIdleCx;
    eye.cy = kIdleCy;
    eye.r = kIdleR;
    eye.cut_r = 0.0f;
    eye.cut_dy = 0.0f;
    if (mood == kRoboEyesHappy) {
        eye.cx = kHappyCx;
        eye.cy = kHappyCy;
        eye.r = kHappyR;
        eye.cut_r = kHappyCutR;
        eye.cut_dy = kHappyCutDy;
    } else if (mood == kRoboEyesSurprised) {
        eye.r = 42.0f;
    } else if (mood == kRoboEyesTired) {
        eye.cy += 8.0f;
    }
    if (index != 0) {
        eye.cx += kEyeSpacing;
    }
    eye.cx += gaze_x;
    eye.cy += gaze_y + bounce;
    return eye;
}

float MoodCoverage(RoboEyesMood mood, int index, float x, float y, const EyeGeom& eye) {
    float coverage = CircleCov(x, y, eye.cx, eye.cy, eye.r);
    if (coverage <= 0.0f) {
        return 0.0f;
    }
    if (mood == kRoboEyesHappy && eye.cut_r > 0.0f) {
        coverage -= CircleCov(x, y, eye.cx, eye.cy + eye.cut_dy, eye.cut_r);
    } else if (mood == kRoboEyesAngry) {
        // Slant the upper lid down toward the nose.
        float side = (index == 0) ? 1.0f : -1.0f;
        float y_line = (eye.cy - 8.0f) + side * 0.42f * (x - eye.cx);
        float below = y - y_line;
        float lid = Clampf(below * 0.85f + 0.5f, 0.0f, 1.0f);
        coverage *= lid;
    } else if (mood == kRoboEyesTired) {
        // Flat upper lid just below the midline, so the eye reads as half-closed.
        float dx = x - eye.cx;
        float y_line = eye.cy + 4.0f + 0.004f * dx * dx;
        float below = y - y_line;
        coverage *= Clampf(below * 1.1f + 0.5f, 0.0f, 1.0f);
    }
    if (coverage < 0.0f) {
        return 0.0f;
    }
    if (coverage > 1.0f) {
        return 1.0f;
    }
    return coverage;
}

uint16_t PackEye(float coverage) {
    int r = static_cast<int>(kEyeR * coverage + 0.5f);
    int g = static_cast<int>(kEyeG * coverage + 0.5f);
    int b = static_cast<int>(kEyeB * coverage + 0.5f);
    if (r > 255) {
        r = 255;
    }
    if (g > 255) {
        g = 255;
    }
    if (b > 255) {
        b = 255;
    }
    return static_cast<uint16_t>(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3));
}

bool OnGlass(float x, float y) {
    float dx = x - kScreenCx;
    float dy = y - kScreenCy;
    return dx * dx + dy * dy <= kScreenR * kScreenR;
}

void DrawEye(uint16_t* pixels, int width, int height, int stride_px, const RoboEyesFace* face,
             int index) {
    float open = (index == 0) ? face->open_l : face->open_r;
    if (open < 0.03f) {
        open = 0.03f;
    }
    if (open > 1.0f) {
        open = 1.0f;
    }
    EyeGeom eye = EyeGeometry(face->mood, index, face->gaze_x, face->gaze_y, face->bounce);
    float pivot = eye.cy;
    int x0 = static_cast<int>(eye.cx - eye.r - 2.0f);
    int x1 = static_cast<int>(eye.cx + eye.r + 2.0f);
    int y0 = static_cast<int>(pivot - eye.r * open - 2.0f);
    int y1 = static_cast<int>(pivot + eye.r * open + 2.0f);
    if (x0 < 0) {
        x0 = 0;
    }
    if (y0 < 0) {
        y0 = 0;
    }
    if (x1 >= width) {
        x1 = width - 1;
    }
    if (y1 >= height) {
        y1 = height - 1;
    }
    for (int y = y0; y <= y1; ++y) {
        uint16_t* row = pixels + static_cast<int>(y * stride_px);
        float py = static_cast<float>(y) + 0.5f;
        for (int x = x0; x <= x1; ++x) {
            float px = static_cast<float>(x) + 0.5f;
            if (!OnGlass(px, py)) {
                continue;
            }
            float coverage;
            if (open < 0.08f) {
                float ry = eye.r * open;
                if (ry < 0.65f) {
                    ry = 0.65f;
                }
                coverage = EllipseCov(px, py, eye.cx, pivot, eye.r, ry);
            } else {
                float shape_y = pivot + (py - pivot) / open;
                coverage = MoodCoverage(face->mood, index, px, shape_y, eye);
            }
            if (coverage <= 0.004f) {
                continue;
            }
            row[x] = PackEye(coverage);
        }
    }
}

bool GazeFits(float gaze_x, float gaze_y) {
    // Keep each eye center inside the round glass, allowing the rim to crop it
    // the way the reference look-around does.
    constexpr float kLimit2 = 102.0f * 102.0f;
    float dy = (kIdleCy + gaze_y) - kScreenCy;
    float dx_l = (kIdleCx + gaze_x) - kScreenCx;
    float dx_r = (kIdleCx + kEyeSpacing + gaze_x) - kScreenCx;
    return dx_l * dx_l + dy * dy <= kLimit2 && dx_r * dx_r + dy * dy <= kLimit2;
}

void BeginMove(RoboEyesFace* face, float to_x, float to_y, int duration_ms) {
    face->gaze_from_x = face->gaze_x;
    face->gaze_from_y = face->gaze_y;
    face->gaze_to_x = to_x;
    face->gaze_to_y = to_y;
    face->gaze_move_t_ms = 0;
    face->gaze_move_dur_ms = duration_ms;
    face->gaze_mode = 1;
}

void PickWanderTarget(RoboEyesFace* face) {
    // Idle / listening sometimes returns to center, the way the reference loop does.
    // Speaking keeps moving so a long reply does not sit still.
    if (face->attention != kRoboEyesSpeak && (NextRand(face) % 5u) == 0u) {
        BeginMove(face, 0.0f, 0.0f, 260);
        return;
    }
    float x_lo = -30.0f;
    float x_hi = 36.0f;
    float y_lo = -34.0f;
    float y_hi = 74.0f;
    if (face->attention == kRoboEyesSpeak) {
        x_lo = -22.0f;
        x_hi = 26.0f;
        y_lo = -20.0f;
        y_hi = 36.0f;
    }
    if (face->mood == kRoboEyesAngry) {
        x_lo = -16.0f;
        x_hi = 16.0f;
        y_lo = -18.0f;
        y_hi = 12.0f;
    } else if (face->mood == kRoboEyesTired) {
        x_lo = -14.0f;
        x_hi = 14.0f;
        y_lo = 6.0f;
        y_hi = 28.0f;
    }
    for (int attempt = 0; attempt < 12; ++attempt) {
        float gx = RandRange(face, x_lo, x_hi);
        float gy = RandRange(face, y_lo, y_hi);
        if (GazeFits(gx, gy)) {
            BeginMove(face, gx, gy, 280);
            return;
        }
    }
    BeginMove(face, 0.0f, 0.0f, 280);
}

bool AllowsWander(const RoboEyesFace* face) {
    // Listening uses the same attention as idle. Speaking still glances, just less far.
    // Happy and surprised keep their eye shape, but they no longer pin the gaze:
    // a conversation is almost entirely listening or speaking.
    return face->attention == kRoboEyesWander || face->attention == kRoboEyesSpeak;
}

void AdvanceGaze(RoboEyesFace* face, uint32_t dt) {
    if (AllowsWander(face)) {
        if (face->gaze_mode == 0) {
            face->gaze_hold_ms -= static_cast<int>(dt);
            if (face->gaze_hold_ms <= 0) {
                PickWanderTarget(face);
            }
        }
    } else {
        float want_x = 0.0f;
        float want_y = 0.0f;
        if (face->attention == kRoboEyesThink) {
            want_x = 6.0f;
            want_y = -30.0f;
        }
        bool same_goal =
            fabsf(face->gaze_to_x - want_x) <= 0.5f && fabsf(face->gaze_to_y - want_y) <= 0.5f;
        bool arrived = fabsf(face->gaze_x - want_x) <= 0.6f && fabsf(face->gaze_y - want_y) <= 0.6f;
        if (!(face->gaze_mode == 1 && same_goal) && !(face->gaze_mode == 0 && arrived)) {
            BeginMove(face, want_x, want_y, 240);
        }
    }

    if (face->gaze_mode != 1) {
        return;
    }
    face->gaze_move_t_ms += static_cast<int>(dt);
    float u = 1.0f;
    if (face->gaze_move_dur_ms > 0) {
        u = static_cast<float>(face->gaze_move_t_ms) / static_cast<float>(face->gaze_move_dur_ms);
    }
    if (u >= 1.0f) {
        u = 1.0f;
        face->gaze_mode = 0;
        if (AllowsWander(face)) {
            face->gaze_hold_ms = 420 + static_cast<int>(NextRand(face) % 800u);
        } else {
            face->gaze_hold_ms = 100000;
        }
    }
    float remain = 1.0f - u;
    float eased = 1.0f - remain * remain * remain;
    face->gaze_x = face->gaze_from_x + (face->gaze_to_x - face->gaze_from_x) * eased;
    face->gaze_y = face->gaze_from_y + (face->gaze_to_y - face->gaze_from_y) * eased;
}

}  // namespace

void RoboEyesInit(RoboEyesFace* face, uint32_t seed) {
    memset(face, 0, sizeof(*face));
    face->mood = kRoboEyesIdle;
    face->attention = kRoboEyesWander;
    face->open_l = 1.0f;
    face->open_r = 1.0f;
    face->blink_t_ms = -1;
    face->next_blink_ms = 1100;
    face->gaze_mode = 0;
    face->gaze_hold_ms = 700;
    face->rng = seed != 0 ? seed : 0x6D2B79F5u;
}

void RoboEyesSetMood(RoboEyesFace* face, RoboEyesMood mood) {
    if (face->mood != mood) {
        face->mood = mood;
        face->wink_eye = 0;
    }
}

void RoboEyesSetAttention(RoboEyesFace* face, RoboEyesAttention attention) {
    if (face->attention == attention) {
        return;
    }
    bool was_looking = face->attention == kRoboEyesWander || face->attention == kRoboEyesSpeak;
    face->attention = attention;
    bool will_look = attention == kRoboEyesWander || attention == kRoboEyesSpeak;
    // Leaving a locked pose (connecting / thinking) must not inherit its long hold,
    // or the first saccade waits until that timer expires.
    if (will_look && !was_looking) {
        face->gaze_mode = 0;
        face->gaze_hold_ms = 200;
    }
}

void RoboEyesWink(RoboEyesFace* face, int eye) {
    if (eye != 1 && eye != 2) {
        return;
    }
    face->wink_eye = eye;
    face->blink_t_ms = 0;
}

void RoboEyesTick(RoboEyesFace* face, uint32_t dt_ms) {
    if (dt_ms > 100) {
        dt_ms = 100;
    }
    face->laugh_ms += static_cast<int>(dt_ms);
    if (face->laugh_ms > 1000000) {
        face->laugh_ms -= 1000000;
    }

    if (face->blink_t_ms >= 0) {
        face->blink_t_ms += static_cast<int>(dt_ms);
        if (face->blink_t_ms >= kBlinkEndMs) {
            face->blink_t_ms = -1;
            face->wink_eye = 0;
            face->next_blink_ms = 1600 + static_cast<int>(NextRand(face) % 1800u);
        }
    } else if (face->next_blink_ms > static_cast<int>(dt_ms)) {
        face->next_blink_ms -= static_cast<int>(dt_ms);
    } else {
        face->blink_t_ms = 0;
        face->next_blink_ms = 0;
    }

    float blink_open = (face->blink_t_ms < 0) ? 1.0f : BlinkOpen(face->blink_t_ms);
    if (face->wink_eye == 1) {
        face->open_l = blink_open;
        face->open_r = 1.0f;
    } else if (face->wink_eye == 2) {
        face->open_l = 1.0f;
        face->open_r = blink_open;
    } else {
        face->open_l = blink_open;
        face->open_r = blink_open;
    }

    AdvanceGaze(face, dt_ms);

    float seconds = static_cast<float>(face->laugh_ms) * 0.001f;
    face->bounce = 0.0f;
    if (face->mood == kRoboEyesHappy) {
        face->bounce = sinf(seconds * 6.2831853f / 0.70f) * 3.5f;
    } else if (face->attention == kRoboEyesSpeak) {
        face->bounce = sinf(seconds * 6.2831853f / 0.32f) * 2.2f;
    }
}

void RoboEyesRender(const RoboEyesFace* face, uint16_t* pixels, int width, int height,
                    int stride_px) {
    if (face == nullptr || pixels == nullptr || width <= 0 || height <= 0 || stride_px < width) {
        return;
    }
    memset(pixels, 0,
           sizeof(uint16_t) * static_cast<size_t>(stride_px) * static_cast<size_t>(height));
    DrawEye(pixels, width, height, stride_px, face, 0);
    DrawEye(pixels, width, height, stride_px, face, 1);
}

#ifdef ROBOEYES_FACE_HOST
static RoboEyesMood ParseMood(const char* name) {
    if (name == nullptr) {
        return kRoboEyesIdle;
    }
    if (strcmp(name, "happy") == 0) {
        return kRoboEyesHappy;
    }
    if (strcmp(name, "angry") == 0) {
        return kRoboEyesAngry;
    }
    if (strcmp(name, "tired") == 0) {
        return kRoboEyesTired;
    }
    if (strcmp(name, "surprised") == 0) {
        return kRoboEyesSurprised;
    }
    return kRoboEyesIdle;
}

struct GazeSpan {
    float x;
    float y;
    float min_open;
    int blinks;
};

GazeSpan MeasureGaze(RoboEyesAttention attention, RoboEyesMood mood, int duration_ms) {
    RoboEyesFace face;
    RoboEyesInit(&face, 1);
    RoboEyesSetMood(&face, mood);
    RoboEyesSetAttention(&face, attention);
    GazeSpan span = {0.0f, 0.0f, 1.0f, 0};
    float min_x = 0.0f;
    float max_x = 0.0f;
    float min_y = 0.0f;
    float max_y = 0.0f;
    int prev_blink = -1;
    for (int t = 0; t < duration_ms; t += 66) {
        RoboEyesTick(&face, 66);
        if (face.gaze_x < min_x) {
            min_x = face.gaze_x;
        }
        if (face.gaze_x > max_x) {
            max_x = face.gaze_x;
        }
        if (face.gaze_y < min_y) {
            min_y = face.gaze_y;
        }
        if (face.gaze_y > max_y) {
            max_y = face.gaze_y;
        }
        if (face.open_l < span.min_open) {
            span.min_open = face.open_l;
        }
        if (face.blink_t_ms >= 0 && prev_blink < 0) {
            span.blinks++;
        }
        prev_blink = face.blink_t_ms;
    }
    span.x = max_x - min_x;
    span.y = max_y - min_y;
    return span;
}

int main(int argc, char** argv) {
    const char* mood_name = argc > 1 ? argv[1] : "idle";
    if (strcmp(mood_name, "ticks") == 0) {
        // Listening uses the same attention as idle. Both must travel.
        GazeSpan idle = MeasureGaze(kRoboEyesWander, kRoboEyesIdle, 8000);
        GazeSpan listen = MeasureGaze(kRoboEyesWander, kRoboEyesIdle, 8000);
        GazeSpan speaking = MeasureGaze(kRoboEyesSpeak, kRoboEyesHappy, 8000);
        GazeSpan center = MeasureGaze(kRoboEyesCenter, kRoboEyesIdle, 4000);
        printf(
            "idle_span=%.1f,%.1f listen_span=%.1f,%.1f speak_span=%.1f,%.1f "
            "center_span=%.1f,%.1f blinks=%d min_open=%.3f\n",
            idle.x, idle.y, listen.x, listen.y, speaking.x, speaking.y, center.x, center.y,
            idle.blinks, idle.min_open);
        bool ok = idle.blinks >= 2 && idle.min_open < 0.1f && idle.x > 40.0f && idle.y > 40.0f &&
                  listen.x > 40.0f && listen.y > 40.0f && speaking.x > 20.0f &&
                  speaking.y > 20.0f && center.x < 2.0f && center.y < 2.0f;
        return ok ? 0 : 1;
    }
    float open = argc > 2 ? static_cast<float>(atof(argv[2])) : 1.0f;
    float gaze_x = argc > 3 ? static_cast<float>(atof(argv[3])) : 0.0f;
    float gaze_y = argc > 4 ? static_cast<float>(atof(argv[4])) : 0.0f;
    const char* path = argc > 5 ? argv[5] : "/tmp/roboeyes-preview.ppm";

    RoboEyesFace face;
    RoboEyesInit(&face, 1);
    RoboEyesSetMood(&face, ParseMood(mood_name));
    face.open_l = open;
    face.open_r = open;
    face.gaze_x = gaze_x;
    face.gaze_y = gaze_y;
    face.bounce = 0.0f;

    static uint16_t pixels[240 * 240];
    RoboEyesRender(&face, pixels, 240, 240, 240);

    FILE* out = fopen(path, "wb");
    if (out == nullptr) {
        return 1;
    }
    fprintf(out, "P6\n240 240\n255\n");
    for (int i = 0; i < 240 * 240; ++i) {
        uint16_t p = pixels[i];
        uint8_t r5 = (p >> 11) & 0x1F;
        uint8_t g6 = (p >> 5) & 0x3F;
        uint8_t b5 = p & 0x1F;
        uint8_t rgb[3] = {static_cast<uint8_t>((r5 << 3) | (r5 >> 2)),
                          static_cast<uint8_t>((g6 << 2) | (g6 >> 4)),
                          static_cast<uint8_t>((b5 << 3) | (b5 >> 2))};
        fwrite(rgb, 1, 3, out);
    }
    fclose(out);
    return 0;
}
#endif  // ROBOEYES_FACE_HOST
#endif  // ROBOEYES_FACE_HOST || CONFIG_LCD_GC9A01_240X240
