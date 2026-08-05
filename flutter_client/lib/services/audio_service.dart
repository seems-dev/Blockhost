import 'package:audioplayers/audioplayers.dart';

/// AudioService manages all sound in the app.
///
/// IMPORTANT - Chrome autoplay policy:
/// Browsers block audio playback until the user has interacted with the page.
/// Therefore we defer startAmbient() until the FIRST user tap via [onFirstInteraction].
class AudioService {
  static final AudioPlayer _ambient = AudioPlayer();
  static final List<AudioPlayer> _clickPool =
      List.generate(3, (_) => AudioPlayer());
  static int _clickIndex = 0;

  static bool _ambientStarted = false;
  static bool _interacted = false;
  static bool ambientEnabled = true;

  static Future<void> init() async {
    await _ambient.setReleaseMode(ReleaseMode.loop);
    await _ambient.setVolume(0.30);
  }

  /// Call this on the FIRST user interaction (tap) to unlock browser audio.
  /// Chrome will block any audio fired before a user gesture.
  static Future<void> onFirstInteraction() async {
    if (_interacted) return;
    _interacted = true;
    await startAmbient();
  }

  /// Start looping ambient pond music (requires prior user interaction on web).
  static Future startAmbient() async {
  print(
    'startAmbient called | enabled=$ambientEnabled | started=$_ambientStarted',
  );

  if (! _interacted) return;
  if (! ambientEnabled) return;
  if (_ambientStarted) return;

  try {
    await _ambient.play(
      AssetSource('ambient_pond.mp3'),
    );

    _ambientStarted = true;
  } catch (e) {}
}
  /// Play the water-drop UI click sound.
  static Future<void> playClick() async {
    // Unlock ambient on first real tap
    await onFirstInteraction();
    try {
      final player = _clickPool[_clickIndex % _clickPool.length];
      _clickIndex++;
      await player.stop();
      await player.setVolume(0.7);
      await player.play(AssetSource('audio/ui_click_water.wav'));
    } catch (e) {
      // Ignore
    }
  }

  /// Play success chime after login / server created.
  static Future<void> playSuccess() async {
    try {
      final player = AudioPlayer();
      await player.setVolume(0.8);
      await player.play(AssetSource('ui_success_chime.mp3'));
      player.onPlayerComplete.listen((_) => player.dispose());
    } catch (e) {
      // Ignore
    }
  }

 static Future stopAmbient() async {
  print('STOPPING AMBIENT');

  await _ambient.stop();

  _ambientStarted = false;

  print('AMBIENT STOPPED');
}

  static Future setAmbientVolume(double volume) async {
  await _ambient.setVolume(volume);
}
}
