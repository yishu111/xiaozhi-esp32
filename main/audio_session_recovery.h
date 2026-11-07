#ifndef AUDIO_SESSION_RECOVERY_H_
#define AUDIO_SESSION_RECOVERY_H_

#include "device_state.h"

#include <cstdint>

// Listening and speaking keep the microphone path in uplink mode. Listening
// also turns wake-word detection off. Those states are only recoverable when
// the audio channel is still open; otherwise the device must return to idle,
// which is the state that arms wake-word detection again.

enum class AudioSessionRecovery {
    kNone = 0,
    kReturnToIdle,
};

inline bool AudioSessionRequiresOpenChannel(DeviceState state) {
    return state == kDeviceStateListening || state == kDeviceStateSpeaking;
}

inline AudioSessionRecovery DecideAudioSessionRecovery(DeviceState state, bool channel_open) {
    if (!AudioSessionRequiresOpenChannel(state) || channel_open) {
        return AudioSessionRecovery::kNone;
    }
    return AudioSessionRecovery::kReturnToIdle;
}

// A close notification captured `epoch` when the socket closed. A newer
// wake/connect attempt increments the epoch and must not be forced back to
// idle by that stale notification. Connecting is included because a socket
// that dies while OpenAudioChannel is in progress should still return to idle
// unless a newer attempt has already started.
inline bool AudioChannelCloseMayReturnToIdle(std::uint32_t epoch, std::uint32_t current_epoch,
                                             DeviceState state) {
    if (epoch != current_epoch) {
        return false;
    }
    return state == kDeviceStateConnecting || state == kDeviceStateListening ||
           state == kDeviceStateSpeaking;
}

// Socket callbacks carry the generation returned when that socket was created.
// CloseAudioChannel and OpenAudioChannel increment the generation before
// destroying the previous socket, so a destructor callback cannot retire the
// new session.
inline bool WebSocketCloseEventIsCurrent(std::uint32_t event_generation,
                                         std::uint32_t current_generation) {
    return event_generation == current_generation;
}

#endif  // AUDIO_SESSION_RECOVERY_H_
