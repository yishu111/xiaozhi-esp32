#include "roboeyes_display.h"

#if CONFIG_LCD_GC9A01_240X240

#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_random.h>

#include <cctype>
#include <cstring>

#include "assets/lang_config.h"

#define TAG "RoboEyes"

namespace {

bool IsClockText(const char* status) {
    if (status == nullptr || std::strlen(status) != 5 || status[2] != ':') {
        return false;
    }
    for (int i = 0; i < 5; ++i) {
        if (i == 2) {
            continue;
        }
        if (!std::isdigit(static_cast<unsigned char>(status[i]))) {
            return false;
        }
    }
    return true;
}

bool StatusTextVisible(const char* status) {
    if (status == nullptr || status[0] == '\0' || IsClockText(status)) {
        return false;
    }
    return std::strcmp(status, Lang::Strings::INITIALIZING) != 0 &&
           std::strcmp(status, Lang::Strings::STANDBY) != 0 &&
           std::strcmp(status, Lang::Strings::CONNECTING) != 0 &&
           std::strcmp(status, Lang::Strings::LISTENING) != 0 &&
           std::strcmp(status, Lang::Strings::SPEAKING) != 0 &&
           std::strcmp(status, Lang::Strings::CHECKING_NEW_VERSION) != 0 &&
           std::strcmp(status, Lang::Strings::LOADING_PROTOCOL) != 0;
}

RoboEyesAttention AttentionFromStatus(const char* status) {
    if (std::strcmp(status, Lang::Strings::LISTENING) == 0 ||
        std::strcmp(status, Lang::Strings::CONNECTING) == 0) {
        return kRoboEyesCenter;
    }
    if (std::strcmp(status, Lang::Strings::SPEAKING) == 0) {
        return kRoboEyesSpeak;
    }
    return kRoboEyesWander;
}

}  // namespace

RoboEyesLcdDisplay::RoboEyesLcdDisplay(esp_lcd_panel_io_handle_t panel_io,
                                       esp_lcd_panel_handle_t panel, int width, int height,
                                       int offset_x, int offset_y, bool mirror_x, bool mirror_y,
                                       bool swap_xy)
    : SpiLcdDisplay(panel_io, panel, width, height, offset_x, offset_y, mirror_x, mirror_y,
                    swap_xy) {}

RoboEyesLcdDisplay::~RoboEyesLcdDisplay() {
    if (timer_ != nullptr || canvas_ != nullptr) {
        DisplayLockGuard lock(this);
        if (lock) {
            if (timer_ != nullptr) {
                lv_timer_delete(timer_);
                timer_ = nullptr;
            }
            if (canvas_ != nullptr) {
                lv_obj_del(canvas_);
                canvas_ = nullptr;
            }
        }
    }
    if (canvas_ == nullptr && frame_ != nullptr) {
        heap_caps_free(frame_);
        frame_ = nullptr;
    }
}

bool RoboEyesLcdDisplay::CreateCanvas() {
    uint32_t stride_bytes =
        lv_draw_buf_width_to_stride(static_cast<uint32_t>(width_), LV_COLOR_FORMAT_RGB565);
    if (stride_bytes < static_cast<uint32_t>(width_) * 2u || (stride_bytes % 2u) != 0u) {
        ESP_LOGE(TAG, "Unexpected canvas stride %u", static_cast<unsigned>(stride_bytes));
        return false;
    }
    size_t bytes = static_cast<size_t>(stride_bytes) * static_cast<size_t>(height_);
    size_t align = LV_DRAW_BUF_ALIGN;
    if (align < 4) {
        align = 4;
    }
    frame_ = static_cast<uint16_t*>(
        heap_caps_aligned_alloc(align, bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (frame_ == nullptr) {
        frame_ = static_cast<uint16_t*>(heap_caps_aligned_alloc(align, bytes, MALLOC_CAP_8BIT));
    }
    if (frame_ == nullptr) {
        ESP_LOGE(TAG, "RoboEyes buffer alloc failed (%u bytes)", static_cast<unsigned>(bytes));
        return false;
    }
    memset(frame_, 0, bytes);
    stride_px_ = static_cast<int>(stride_bytes / 2u);

    canvas_ = lv_canvas_create(lv_screen_active());
    if (canvas_ == nullptr) {
        heap_caps_free(frame_);
        frame_ = nullptr;
        ESP_LOGE(TAG, "lv_canvas_create failed");
        return false;
    }
    lv_canvas_set_buffer(canvas_, frame_, width_, height_, LV_COLOR_FORMAT_RGB565);
    lv_obj_set_size(canvas_, width_, height_);
    lv_obj_align(canvas_, LV_ALIGN_CENTER, 0, 0);
    lv_obj_remove_flag(canvas_, LV_OBJ_FLAG_SCROLLABLE);
    return true;
}

void RoboEyesLcdDisplay::ApplyFaceChrome() {
    lv_obj_t* screen = lv_screen_active();
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x000000), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);

    if (container_ != nullptr) {
        lv_obj_add_flag(container_, LV_OBJ_FLAG_HIDDEN);
    }
    if (emoji_box_ != nullptr) {
        lv_obj_add_flag(emoji_box_, LV_OBJ_FLAG_HIDDEN);
    }
    if (top_bar_ != nullptr) {
        lv_obj_add_flag(top_bar_, LV_OBJ_FLAG_HIDDEN);
    }
    if (preview_image_ != nullptr) {
        lv_obj_add_flag(preview_image_, LV_OBJ_FLAG_HIDDEN);
    }
    if (canvas_ != nullptr) {
        lv_obj_move_foreground(canvas_);
    }

    const lv_color_t eye = lv_color_hex(0x78E6FF);
    if (status_bar_ != nullptr) {
        lv_obj_set_width(status_bar_, 156);
        lv_obj_set_style_bg_opa(status_bar_, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(status_bar_, 0, 0);
        lv_obj_align(status_bar_, LV_ALIGN_TOP_MID, 0, 28);
        lv_obj_move_foreground(status_bar_);
    }
    if (status_label_ != nullptr) {
        lv_obj_set_width(status_label_, 148);
        lv_obj_set_style_text_color(status_label_, eye, 0);
    }
    if (notification_label_ != nullptr) {
        lv_obj_set_width(notification_label_, 148);
        lv_obj_set_style_text_color(notification_label_, lv_color_hex(0xFFFFFF), 0);
    }
    if (bottom_bar_ != nullptr) {
        lv_obj_set_width(bottom_bar_, 168);
        lv_obj_set_style_bg_opa(bottom_bar_, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(bottom_bar_, 0, 0);
        lv_obj_align(bottom_bar_, LV_ALIGN_BOTTOM_MID, 0, -26);
        lv_obj_move_foreground(bottom_bar_);
    }
    if (chat_message_label_ != nullptr) {
        lv_obj_set_width(chat_message_label_, 156);
        lv_obj_set_style_text_color(chat_message_label_, lv_color_hex(0xFFFFFF), 0);
    }
    if (low_battery_popup_ != nullptr) {
        lv_obj_move_foreground(low_battery_popup_);
    }
}

void RoboEyesLcdDisplay::ApplyAttention() {
    RoboEyesAttention attention = kRoboEyesWander;
    if (status_attention_ == kRoboEyesCenter || status_attention_ == kRoboEyesSpeak) {
        attention = status_attention_;
    } else if (emotion_attention_ == kRoboEyesThink) {
        attention = kRoboEyesThink;
    }
    RoboEyesSetAttention(&face_, attention);
}

void RoboEyesLcdDisplay::DrawFace() {
    if (frame_ == nullptr || canvas_ == nullptr) {
        return;
    }
    RoboEyesRender(&face_, frame_, width_, height_, stride_px_);
    lv_obj_invalidate(canvas_);
}

void RoboEyesLcdDisplay::OnTimer() {
    uint32_t now = lv_tick_get();
    uint32_t dt = now - last_tick_ms_;
    last_tick_ms_ = now;
    RoboEyesTick(&face_, dt);
    DrawFace();
}

void RoboEyesLcdDisplay::SetupUI() {
    if (setup_ui_called_) {
        ESP_LOGW(TAG, "SetupUI() called multiple times, skipping duplicate call");
        return;
    }

    SpiLcdDisplay::SetupUI();

    DisplayLockGuard lock(this);
    if (!lock) {
        return;
    }
    if (width_ != 240 || height_ != 240) {
        ESP_LOGW(TAG, "RoboEyes geometry is tuned for 240x240 (got %dx%d)", width_, height_);
    }
    if (!CreateCanvas()) {
        ESP_LOGE(TAG, "Keeping the default LCD UI");
        return;
    }

    RoboEyesInit(&face_, esp_random());
    ApplyFaceChrome();
    DrawFace();
    last_tick_ms_ = lv_tick_get();
    timer_ = lv_timer_create(
        [](lv_timer_t* timer) {
            auto* self = static_cast<RoboEyesLcdDisplay*>(lv_timer_get_user_data(timer));
            self->OnTimer();
        },
        66, this);
    if (timer_ == nullptr) {
        ESP_LOGE(TAG, "Failed to create RoboEyes timer");
    }
}

bool RoboEyesLcdDisplay::SetEmotion(const char* emotion) {
    if (emotion == nullptr || emotion[0] == '\0') {
        return false;
    }
    if (canvas_ == nullptr) {
        return LcdDisplay::SetEmotion(emotion);
    }

    RoboEyesMood mood = kRoboEyesIdle;
    RoboEyesAttention emotion_attention = kRoboEyesWander;
    bool wink = false;
    if (std::strcmp(emotion, "happy") == 0 || std::strcmp(emotion, "laughing") == 0 ||
        std::strcmp(emotion, "laugh") == 0 || std::strcmp(emotion, "funny") == 0 ||
        std::strcmp(emotion, "loving") == 0 || std::strcmp(emotion, "kissy") == 0 ||
        std::strcmp(emotion, "delicious") == 0 || std::strcmp(emotion, "confident") == 0 ||
        std::strcmp(emotion, "cool") == 0 || std::strcmp(emotion, "relaxed") == 0 ||
        std::strcmp(emotion, "embarrassed") == 0 || std::strcmp(emotion, "winking") == 0) {
        mood = kRoboEyesHappy;
        wink = std::strcmp(emotion, "winking") == 0;
    } else if (std::strcmp(emotion, "angry") == 0) {
        mood = kRoboEyesAngry;
    } else if (std::strcmp(emotion, "sad") == 0 || std::strcmp(emotion, "crying") == 0 ||
               std::strcmp(emotion, "sleepy") == 0 || std::strcmp(emotion, "tired") == 0 ||
               std::strcmp(emotion, "silly") == 0) {
        mood = kRoboEyesTired;
    } else if (std::strcmp(emotion, "surprised") == 0 || std::strcmp(emotion, "shocked") == 0) {
        mood = kRoboEyesSurprised;
    } else if (std::strcmp(emotion, "thinking") == 0 || std::strcmp(emotion, "confused") == 0) {
        emotion_attention = kRoboEyesThink;
    } else if (std::strcmp(emotion, "neutral") != 0 && std::strcmp(emotion, "robot_2") != 0) {
        ESP_LOGD(TAG, "Unmapped emotion '%s', showing idle eyes", emotion);
    }

    DisplayLockGuard lock(this);
    if (!lock) {
        return false;
    }
    RoboEyesSetMood(&face_, mood);
    face_.wink_eye = 0;
    emotion_attention_ = emotion_attention;
    ApplyAttention();
    if (wink) {
        RoboEyesWink(&face_, 1);
    }
    return true;
}

void RoboEyesLcdDisplay::SetStatus(const char* status) {
    if (status == nullptr) {
        return;
    }
    if (canvas_ == nullptr) {
        SpiLcdDisplay::SetStatus(status);
        return;
    }

    SpiLcdDisplay::SetStatus(StatusTextVisible(status) ? status : "");
    status_attention_ = AttentionFromStatus(status);
    DisplayLockGuard lock(this);
    if (lock) {
        ApplyAttention();
    }
}

void RoboEyesLcdDisplay::SetTheme(Theme* theme) {
    SpiLcdDisplay::SetTheme(theme);
    if (canvas_ == nullptr) {
        return;
    }
    DisplayLockGuard lock(this);
    if (lock) {
        ApplyFaceChrome();
    }
}

void RoboEyesLcdDisplay::SetPreviewImage(std::unique_ptr<LvglImage> image) {
    if (canvas_ == nullptr) {
        LcdDisplay::SetPreviewImage(std::move(image));
    }
}

#endif
