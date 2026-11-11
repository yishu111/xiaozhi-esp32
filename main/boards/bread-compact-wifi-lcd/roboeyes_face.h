#pragma once

#include <stdint.h>

// Original LVGL/RGB565 face for the single 240x240 round GC9A01.
// The look is inspired by FluxGarage RoboEyes (GPL-3.0-only); this file is
// not derived from that source. See NOTICE in this directory.

enum RoboEyesMood {
    kRoboEyesIdle = 0,
    kRoboEyesHappy,
    kRoboEyesAngry,
    kRoboEyesTired,
    kRoboEyesSurprised,
};

// How the gaze behaves between emotion changes.
enum RoboEyesAttention {
    kRoboEyesWander = 0,  // Idle and listening: both eyes saccade together.
    kRoboEyesCenter,      // Connecting: settle in the middle.
    kRoboEyesSpeak,       // Speaking: milder saccades, plus a small bounce.
    kRoboEyesThink,       // Thinking / confused: look up.
};

struct RoboEyesFace {
    RoboEyesMood mood;
    RoboEyesAttention attention;
    int wink_eye;  // 0 none, 1 left, 2 right. One-shot.
    float open_l;
    float open_r;
    float gaze_x;
    float gaze_y;
    float bounce;
    int blink_t_ms;  // <0 when the lids are open.
    int next_blink_ms;
    int laugh_ms;
    int gaze_mode;  // 0 hold, 1 move.
    int gaze_hold_ms;
    int gaze_move_t_ms;
    int gaze_move_dur_ms;
    float gaze_from_x;
    float gaze_from_y;
    float gaze_to_x;
    float gaze_to_y;
    uint32_t rng;
};

void RoboEyesInit(RoboEyesFace* face, uint32_t seed);
void RoboEyesSetMood(RoboEyesFace* face, RoboEyesMood mood);
void RoboEyesSetAttention(RoboEyesFace* face, RoboEyesAttention attention);
void RoboEyesWink(RoboEyesFace* face, int eye);
void RoboEyesTick(RoboEyesFace* face, uint32_t dt_ms);
void RoboEyesRender(const RoboEyesFace* face, uint16_t* pixels, int width, int height,
                    int stride_px);
