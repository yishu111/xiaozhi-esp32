#pragma once

#include "display/lcd_display.h"
#include "roboeyes_face.h"

#if CONFIG_LCD_GC9A01_240X240

// Full-screen RoboEyes-style face for the single GC9A01 round panel.
// Stock emoji widgets stay allocated so status, notifications, and chat text
// still have a place, but they sit above the eye canvas and stay hidden
// unless there is something the user must read (Wi-Fi setup, errors, activation).
class RoboEyesLcdDisplay : public SpiLcdDisplay {
public:
    RoboEyesLcdDisplay(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_handle_t panel, int width,
                       int height, int offset_x, int offset_y, bool mirror_x, bool mirror_y,
                       bool swap_xy);
    ~RoboEyesLcdDisplay() override;

    void SetupUI() override;
    bool SetEmotion(const char* emotion) override;
    void SetStatus(const char* status) override;
    void SetTheme(Theme* theme) override;
    void SetPreviewImage(std::unique_ptr<LvglImage> image) override;

private:
    bool CreateCanvas();
    void ApplyFaceChrome();
    void ApplyAttention();
    void DrawFace();
    void OnTimer();

    RoboEyesFace face_{};
    RoboEyesAttention status_attention_ = kRoboEyesWander;
    RoboEyesAttention emotion_attention_ = kRoboEyesWander;
    lv_obj_t* canvas_ = nullptr;
    lv_timer_t* timer_ = nullptr;
    uint16_t* frame_ = nullptr;
    int stride_px_ = 0;
    uint32_t last_tick_ms_ = 0;
};

#endif
