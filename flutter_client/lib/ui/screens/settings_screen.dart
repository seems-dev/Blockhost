import 'dart:ui';
import 'package:flutter/material.dart';

import '../../state/app_state.dart';
import '../theme/tranquil_theme.dart';
import '../widgets/glass_card.dart';
import '../../services/audio_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.state,
    this.onAuthChanged,
  });

  final AppState state;
  final VoidCallback? onAuthChanged;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  bool pushNotifications = true;
  bool ambientSound = true;
  double ambientVolume = 0.30;

  @override
  void initState() {
    super.initState();
    _loadSettings();
  }

  Future<void> _loadSettings() async {
    final prefs = await SharedPreferences.getInstance();

    final savedNotifications =
        prefs.getBool('push_notifications') ?? true;

    final savedAmbientSound =
        prefs.getBool('ambient_sound') ?? true;

    final savedAmbientVolume =
        prefs.getDouble('ambient_volume') ?? 0.30;

    setState(() {
  pushNotifications = savedNotifications;
  ambientSound = savedAmbientSound;
  ambientVolume = savedAmbientVolume;
});
  AudioService.ambientEnabled = savedAmbientSound;
    await AudioService.setAmbientVolume(savedAmbientVolume);

    if (!savedAmbientSound) {
      await AudioService.stopAmbient();
    }
  }

  Future<void> _saveSettings() async {
    final prefs = await SharedPreferences.getInstance();

    await prefs.setBool(
      'push_notifications',
      pushNotifications,
    );

    await prefs.setBool(
      'ambient_sound',
      ambientSound,
    );

    await prefs.setDouble(
      'ambient_volume',
      ambientVolume,
    );
  }

  bool get _isLoggedIn => (widget.state.accessToken ?? '').isNotEmpty;

  Future<void> _signOut() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
        child: AlertDialog(
          backgroundColor: TranquilTheme.deepWater.withOpacity(0.9),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
            side: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.3)),
          ),
          title: const Text('Sign out?',
              style: TextStyle(color: Colors.white, fontFamily: 'monospace')),
          content: const Text(
            'You will need to sign in again to manage your servers.',
            style: TextStyle(color: Colors.white60, fontSize: 13),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child:
                  const Text('Cancel', style: TextStyle(color: Colors.white38)),
            ),
            TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Sign Out',
                  style: TextStyle(color: Colors.redAccent)),
            ),
          ],
        ),
      ),
    );
    if (confirmed != true || !mounted) return;
    await widget.state.logout();
    widget.onAuthChanged?.call();
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        backgroundColor: TranquilTheme.deepWater,
        content: const Text('Signed out',
            style: TextStyle(
                color: Colors.white, fontFamily: 'monospace', fontSize: 12)),
      ));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: SafeArea(
        child: Column(
          children: [
            // ── Header ───────────────────────────────────────────────────
            _TranquilHeader(
              title: 'Settings',
              icon: Icons.settings_rounded,
              trailing: _AvatarBadge(isLoggedIn: _isLoggedIn),
            ),

            Expanded(
              child: SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 100),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    // ── Account ──────────────────────────────────────────
                    _SectionLabel('ACCOUNT'),
                    const SizedBox(height: 10),
                    GlassCard(
                      child: Column(
                        children: [
                          Row(children: [
                            Container(
                              width: 46,
                              height: 46,
                              decoration: BoxDecoration(
                                color: _isLoggedIn
                                    ? TranquilTheme.glowCyan.withOpacity(.12)
                                    : Colors.white10,
                                borderRadius: BorderRadius.circular(12),
                                border: Border.all(
                                    color: _isLoggedIn
                                        ? TranquilTheme.glowCyan.withOpacity(.4)
                                        : Colors.white12),
                              ),
                              child: Icon(
                                _isLoggedIn
                                    ? Icons.verified_user_rounded
                                    : Icons.person_off_rounded,
                                color: _isLoggedIn
                                    ? TranquilTheme.glowCyan
                                    : Colors.white38,
                                size: 22,
                              ),
                            ),
                            const SizedBox(width: 14),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    _isLoggedIn ? 'Signed In' : 'Not Signed In',
                                    style: const TextStyle(
                                        color: Colors.white,
                                        fontSize: 14,
                                        fontWeight: FontWeight.bold),
                                  ),
                                  const SizedBox(height: 2),
                                  Text(
                                    _isLoggedIn
                                        ? 'Your servers are linked to this device.'
                                        : 'Sign in from the Dashboard tab.',
                                    style: const TextStyle(
                                        color: TranquilTheme.textMuted,
                                        fontSize: 11,
                                        fontFamily: 'monospace'),
                                  ),
                                ],
                              ),
                            ),
                          ]),
                          if (_isLoggedIn) ...[
                            const SizedBox(height: 16),
                            GestureDetector(
                              onTap: _signOut,
                              child: Container(
                                height: 44,
                                decoration: BoxDecoration(
                                  border: Border.all(
                                      color: Colors.redAccent.withOpacity(.5)),
                                  borderRadius: BorderRadius.circular(8),
                                ),
                                child: const Row(
                                  mainAxisAlignment: MainAxisAlignment.center,
                                  children: [
                                    Icon(Icons.logout_rounded,
                                        color: Colors.redAccent, size: 16),
                                    SizedBox(width: 8),
                                    Text('Sign Out',
                                        style: TextStyle(
                                            color: Colors.redAccent,
                                            fontSize: 13,
                                            fontFamily: 'monospace',
                                            fontWeight: FontWeight.w600)),
                                  ],
                                ),
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),

                    const SizedBox(height: 24),

                    // ── Preferences ──────────────────────────────────────
                    _SectionLabel('PREFERENCES'),
                    const SizedBox(height: 10),

                    GlassCard(
                      padding: EdgeInsets.zero,
                      child: Column(
                        children: [
                          _ToggleRow(
                            label: 'Push Notifications',
                            sub: 'Alerts for server status changes',
                            value: pushNotifications,
                            onChanged: (v) async {
                              setState(() => pushNotifications = v);
                              await _saveSettings();
                            },
                          ),

                          Divider(
                            height: 1,
                            color: TranquilTheme.glowCyan.withOpacity(0.12),
                          ),

                          _ToggleRow(
                            label: 'Ambient Sound',
                            sub: 'Play tranquil pond ambience',
                            value: ambientSound,
                            onChanged: (v) async {
  print('Ambient toggled: $v');

  setState(() => ambientSound = v);

  AudioService.ambientEnabled = v;

  print(
    'AudioService.ambientEnabled = ${AudioService.ambientEnabled}',
  );

  await _saveSettings();

  if (v) {
    await AudioService.startAmbient();
  } else {
    await AudioService.stopAmbient();
  }
}
                          ),

                          if (ambientSound)
                            Padding(
                              padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    'Ambient Volume ${(ambientVolume * 100).round()}%',
                                    style: const TextStyle(
                                      color: Colors.white70,
                                      fontSize: 11,
                                      fontFamily: 'monospace',
                                    ),
                                  ),
                                  Slider(
                                    value: ambientVolume,
                                    min: 0,
                                    max: 1,
                                    divisions: 10,
                                    activeColor: TranquilTheme.glowCyan,
                                    onChanged: (v) async {
                                      setState(() => ambientVolume = v);
                                      await AudioService.setAmbientVolume(v);
                                      await _saveSettings();
                                    },
                                  ),
                                ],
                              ),
                            ),
                        ],
                      ),
                    ),

                    const SizedBox(height: 24),

                    // ── About ────────────────────────────────────────────
                    _SectionLabel('ABOUT'),
                    const SizedBox(height: 10),
                    GlassCard(
                      padding: EdgeInsets.zero,
                      child: Column(children: [
                        _AboutRow(label: 'App', value: 'Erex Mobile'),
                        Divider(
                            height: 1,
                            color: TranquilTheme.glowCyan.withOpacity(0.12)),
                        _AboutRow(
                            label: 'Platform', value: 'Minecraft Bedrock'),
                        Divider(
                            height: 1,
                            color: TranquilTheme.glowCyan.withOpacity(0.12)),
                        _AboutRow(label: 'Version', value: 'v1.0.0'),
                      ]),
                    ),

                    const SizedBox(height: 30),

                    // ── Status pill ──────────────────────────────────────
                    Center(
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 16, vertical: 8),
                        decoration: BoxDecoration(
                          border: Border.all(
                              color: TranquilTheme.glowCyan.withOpacity(0.35)),
                          borderRadius: BorderRadius.circular(20),
                          color: TranquilTheme.glowCyan.withOpacity(0.05),
                        ),
                        child: Row(mainAxisSize: MainAxisSize.min, children: [
                          Container(
                              width: 7,
                              height: 7,
                              decoration: BoxDecoration(
                                  color: TranquilTheme.glowCyan,
                                  shape: BoxShape.circle)),
                          const SizedBox(width: 8),
                          Text('SYSTEMS NOMINAL',
                              style: TextStyle(
                                  color: TranquilTheme.glowCyan,
                                  fontSize: 10,
                                  fontFamily: 'monospace',
                                  letterSpacing: 1.5)),
                        ]),
                      ),
                    ),
                    const SizedBox(height: 20),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Shared widgets ─────────────────────────────────────────────────────────────

class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Text(
        text,
        style: TextStyle(
            color: TranquilTheme.textMuted,
            fontSize: 10,
            fontFamily: 'monospace',
            letterSpacing: 1.5),
      );
}

class _AvatarBadge extends StatelessWidget {
  const _AvatarBadge({required this.isLoggedIn});
  final bool isLoggedIn;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 36,
      height: 36,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        border: Border.all(
            color: isLoggedIn ? TranquilTheme.glowCyan : Colors.white24,
            width: 1.5),
        color: TranquilTheme.deepWater.withOpacity(0.5),
      ),
      child: Stack(alignment: Alignment.center, children: [
        const Icon(Icons.person_rounded, color: Colors.white60, size: 18),
        if (isLoggedIn)
          Positioned(
            right: 0,
            bottom: 0,
            child: Container(
              width: 9,
              height: 9,
              decoration: BoxDecoration(
                  color: TranquilTheme.glowCyan, shape: BoxShape.circle),
            ),
          ),
      ]),
    );
  }
}

class _AboutRow extends StatelessWidget {
  _AboutRow({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        child: Row(children: [
          Text(label,
              style: const TextStyle(
                  color: TranquilTheme.textMuted, fontSize: 13)),
          const Spacer(),
          Text(value,
              style: const TextStyle(
                  color: Colors.white,
                  fontSize: 13,
                  fontFamily: 'monospace',
                  fontWeight: FontWeight.w600)),
          const SizedBox(width: 6),
          const Icon(Icons.chevron_right_rounded,
              color: Colors.white24, size: 16),
        ]),
      );
}

class _ToggleRow extends StatelessWidget {
  const _ToggleRow(
      {required this.label,
      required this.sub,
      required this.value,
      required this.onChanged});
  final String label;
  final String sub;
  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(children: [
          Expanded(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(label,
                  style: const TextStyle(
                      color: Colors.white,
                      fontSize: 13,
                      fontWeight: FontWeight.w600)),
              const SizedBox(height: 2),
              Text(sub,
                  style: const TextStyle(
                      color: Colors.white38,
                      fontSize: 11,
                      fontFamily: 'monospace')),
            ]),
          ),
          Switch(
            value: value,
            onChanged: onChanged,
            activeColor: TranquilTheme.glowCyan,
            activeTrackColor: TranquilTheme.glowCyan.withOpacity(.25),
            inactiveThumbColor: Colors.white38,
            inactiveTrackColor: Colors.white10,
          ),
        ]),
      );
}

// ── Shared tranquil header ─────────────────────────────────────────────────────
class _TranquilHeader extends StatelessWidget {
  const _TranquilHeader(
      {required this.title, required this.icon, this.trailing});
  final String title;
  final IconData icon;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
        child: Container(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 14),
          decoration: BoxDecoration(
            color: TranquilTheme.deepWater.withOpacity(0.4),
            border: Border(
              bottom:
                  BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.2)),
            ),
          ),
          child: Row(children: [
            Icon(icon, color: TranquilTheme.glowCyan, size: 20),
            const SizedBox(width: 10),
            Text(title,
                style: const TextStyle(
                    color: Colors.white,
                    fontSize: 18,
                    fontWeight: FontWeight.bold)),
            const Spacer(),
            if (trailing != null)
              trailing!
            else
              Text('EREX',
                  style: TextStyle(
                      color: TranquilTheme.glowCyan,
                      fontSize: 13,
                      fontFamily: 'monospace',
                      letterSpacing: 3)),
          ]),
        ),
      ),
    );
  }
}