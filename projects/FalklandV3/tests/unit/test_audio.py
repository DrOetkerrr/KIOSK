import time

from falklandv3.core.audio import AudioEvent, AudioQueue


def test_audio_queue_retains_recent_events():
    queue = AudioQueue(max_events=2)
    queue.push(AudioEvent(kind="radio", message="Message 1", ts=time.time()))
    queue.push(AudioEvent(kind="alert", message="Alert", ts=time.time()))
    queue.push(AudioEvent(kind="radio", message="Message 2", ts=time.time()))

    events = queue.events()
    assert len(events) == 2
    assert events[-1].message == "Message 2"
    latest_radio = queue.latest("radio")
    assert latest_radio and latest_radio.message == "Message 2"
