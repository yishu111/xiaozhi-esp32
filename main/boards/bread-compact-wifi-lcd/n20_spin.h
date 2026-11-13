#pragma once

#include <stdint.h>
#include <string.h>

// One TB6612 channel for a suspended N20. Default output is off.
// A start is time-bounded and duty-bounded even if the caller disappears.
// There is no encoder, so this cannot detect or clear a mechanical stall.

namespace n20 {

constexpr int kMaxDutyPercent = 30;
constexpr int kMaxRunMs = 800;
constexpr int kRampMs = 200;
constexpr int kTickMs = 50;
constexpr int kPwmHz = 20000;

enum class LineAction {
    kNone,
    kStart,
    kStop,
    kReject,
};

inline LineAction ParseLine(const char* line) {
    if (line == nullptr) {
        return LineAction::kReject;
    }
    if (line[0] == '\0') {
        return LineAction::kNone;
    }
    if (strcmp(line, "motor start") == 0) {
        return LineAction::kStart;
    }
    if (strcmp(line, "motor stop") == 0) {
        return LineAction::kStop;
    }
    return LineAction::kReject;
}

// Assembles one console line. Only '\n' or '\r' completes a command.
// Overflow or a non-printable byte rejects the whole line.
class LineBuffer {
public:
    static constexpr int kCap = 32;

    bool Push(unsigned char ch, LineAction* action) {
        if (ch == '\n' || ch == '\r') {
            bool reject = overflow_ || binary_;
            overflow_ = false;
            binary_ = false;
            buf_[len_] = '\0';
            len_ = 0;
            *action = reject ? LineAction::kReject : ParseLine(buf_);
            return true;
        }
        if (len_ >= kCap - 1) {
            overflow_ = true;
            return false;
        }
        if (ch < 0x20 || ch > 0x7e) {
            binary_ = true;
        }
        buf_[len_++] = static_cast<char>(ch);
        return false;
    }

private:
    char buf_[kCap] = {};
    int len_ = 0;
    bool overflow_ = false;
    bool binary_ = false;
};

struct Output {
    bool stby = false;
    bool ain1 = false;
    bool ain2 = false;
    int duty_percent = 0;
};

inline Output Sanitize(Output in) {
    if (in.duty_percent < 0) {
        in.duty_percent = 0;
    }
    if (in.duty_percent > kMaxDutyPercent) {
        in.duty_percent = kMaxDutyPercent;
    }
    // The only legal drive is forward, STBY on, and a non-zero duty at or under the cap.
    if (!in.stby || in.duty_percent == 0 || !in.ain1 || in.ain2) {
        return Output{};
    }
    return in;
}

enum class CommandResult {
    kIgnored,
    kStarted,
    kBusy,
    kStopped,
    kRejected,
};

class SpinPolicy {
public:
    Output OutputNow(int64_t now_ms) {
        LatchTimeout(now_ms);
        return output_;
    }

    bool ConsumeTimeout() {
        bool timed_out = timed_out_;
        timed_out_ = false;
        return timed_out;
    }

    bool running() const { return running_; }

    int64_t DeadlineMs() const { return running_ ? start_ms_ + kMaxRunMs : 0; }

    // Hardware one-shot. Already-stopped is not a new timeout.
    void NoteDeadline() {
        if (!running_) {
            return;
        }
        ForceOff();
        timed_out_ = true;
    }

    CommandResult Apply(LineAction action, int64_t now_ms) {
        LatchTimeout(now_ms);
        if (action == LineAction::kNone) {
            return CommandResult::kIgnored;
        }
        if (action == LineAction::kReject) {
            return CommandResult::kRejected;
        }
        if (action == LineAction::kStop) {
            ForceOff();
            timed_out_ = false;
            return CommandResult::kStopped;
        }
        if (running_) {
            return CommandResult::kBusy;
        }
        running_ = true;
        start_ms_ = now_ms;
        timed_out_ = false;
        ApplyDrive(now_ms);
        return CommandResult::kStarted;
    }

private:
    void ForceOff() {
        running_ = false;
        output_ = Output{};
    }

    void LatchTimeout(int64_t now_ms) {
        if (!running_) {
            return;
        }
        if (now_ms < start_ms_ || now_ms - start_ms_ >= kMaxRunMs) {
            ForceOff();
            timed_out_ = true;
            return;
        }
        ApplyDrive(now_ms);
    }

    void ApplyDrive(int64_t now_ms) {
        int elapsed = static_cast<int>(now_ms - start_ms_);
        if (elapsed < 0) {
            elapsed = kMaxRunMs;
        }
        int duty = kMaxDutyPercent;
        if (elapsed < kRampMs) {
            duty = kMaxDutyPercent * elapsed / kRampMs;
        }
        if (duty > kMaxDutyPercent) {
            duty = kMaxDutyPercent;
        }
        if (duty < 0) {
            duty = 0;
        }
        output_.stby = duty > 0;
        output_.ain1 = duty > 0;
        output_.ain2 = false;
        output_.duty_percent = duty;
    }

    bool running_ = false;
    bool timed_out_ = false;
    int64_t start_ms_ = 0;
    Output output_{};
};

}  // namespace n20

void InitializeN20Spin();
