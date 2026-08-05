import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:font_awesome_flutter/font_awesome_flutter.dart';

import '../../api/blockhost_api.dart';
import '../../state/app_state.dart';
import '../../services/audio_service.dart';
import '../theme/tranquil_theme.dart';
import '../widgets/glass_card.dart';
import '../widgets/glowing_button.dart';

// ─── Design tokens ────────────────────────────────────────────────────────────
const _bg       = Color(0xFF0A0A0A);
const _surface  = Color(0xFF111111);
const _border   = Color(0xFF1E1E1E);
const _green    = Color(0xFF00FF6A);
const _text     = Color(0xFFEEEEEE);
const _muted    = Color(0xFF555555);
const _mono     = 'monospace';

class LoginScreen extends StatefulWidget {
  const LoginScreen({
    super.key,
    required this.state,
    required this.onLoggedIn,
  });

  final AppState state;
  final VoidCallback onLoggedIn;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final email    = TextEditingController(text: 'test1@example.com');
  final password = TextEditingController(text: 'supersecret123');
  final nickname = TextEditingController(text: 'Tester');

  // Ensure we request `openid` so Google returns an ID token.
  // Use the provided OAuth client ID for web and as the serverClientId for
  // native platforms so ID tokens are tied to your backend.
  static const String _googleClientId = '264249625264-0it828liska1emqu72ebb26s6u6krnmu.apps.googleusercontent.com';

  final GoogleSignIn _googleSignIn = kIsWeb
      ? GoogleSignIn(clientId: _googleClientId, scopes: ['openid', 'email', 'profile'])
      : GoogleSignIn(serverClientId: _googleClientId, scopes: ['openid', 'email', 'profile']);

  String status = '';
  bool busy = false;
  bool _obscure = true;

  @override
  void dispose() {
    email.dispose();
    password.dispose();
    nickname.dispose();
    super.dispose();
  }

  Future<void> _run(Future<Map<String, dynamic>> Function() fn) async {
    setState(() { busy = true; status = ''; });
    try {
      final res = await fn();
      final access  = res['access_token']  as String?;
      final refresh = res['refresh_token'] as String?;
      if (access != null && refresh != null) {
        await widget.state.setTokens(access: access, refresh: refresh);
        AudioService.playSuccess();
        widget.onLoggedIn();
      }
      setState(() => status = 'OK');
    } on ApiException catch (e) {
      setState(() => status = e.message);
    } catch (e) {
      setState(() => status = e.toString());
    } finally {
      setState(() => busy = false);
    }
  }

  Future<void> _handleGoogleSignIn() async {
    try {
      final account = await _googleSignIn.signIn();
      if (account == null) { setState(() => status = 'Cancelled'); return; }
      final auth    = await account.authentication;
      final idToken = auth.idToken;
      if (idToken == null) throw Exception('ID token missing');
      await _run(() => widget.state.api.googleLogin(idToken: idToken));
    } on ApiException catch (e) {
      setState(() => status = e.message);
    } catch (e) {
      setState(() => status = 'Google login failed: $e');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      resizeToAvoidBottomInset: true,
      backgroundColor: Colors.transparent,
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              // ── Brand ──────────────────────────────────────────────────────
              const SizedBox(height: 16),
              const Text(
                'EREX',
                style: TextStyle(
                  color: _green,
                  fontSize: 38,
                  fontWeight: FontWeight.w900,
                  fontFamily: _mono,
                  letterSpacing: 8,
                ),
              ),
              const SizedBox(height: 6),
              const Text(
                'Enterprise hosting for Bedrock creators.',
                style: TextStyle(color: _muted, fontSize: 13, fontFamily: _mono),
              ),
              const SizedBox(height: 36),

              // ── Card ───────────────────────────────────────────────────────
              GlassCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Sign In',
                      style: TextStyle(color: _text, fontSize: 24, fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      'Welcome back. Enter your credentials.',
                      style: TextStyle(color: TranquilTheme.textMuted, fontSize: 12, fontFamily: _mono),
                    ),
                    const SizedBox(height: 28),

                    // Email
                    _FieldLabel('Email Address'),
                    const SizedBox(height: 8),
                    _TermField(
                      controller: email,
                      hint: 'name@enterprise.com',
                      prefixIcon: Icons.alternate_email_rounded,
                      keyboardType: TextInputType.emailAddress,
                    ),
                    const SizedBox(height: 20),

                    // Password
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        _FieldLabel('Password'),
                        GestureDetector(
                          onTap: () {},
                          child: const Text(
                            'Forgot?',
                            style: TextStyle(color: TranquilTheme.glowCyan, fontSize: 12, fontFamily: _mono),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    _TermField(
                      controller: password,
                      hint: '••••••••',
                      prefixIcon: Icons.lock_outline_rounded,
                      obscureText: _obscure,
                      suffixIcon: GestureDetector(
                        onTap: () => setState(() => _obscure = !_obscure),
                        child: Icon(
                          _obscure ? Icons.visibility_off_outlined : Icons.visibility_outlined,
                          color: _muted,
                          size: 18,
                        ),
                      ),
                    ),
                    const SizedBox(height: 28),

                    // Sign In button
                    GlowingButton(
                      label: 'Sign In  →',
                      busy: busy,
                      onTap: () => _run(() => widget.state.api.login(
                        email: email.text,
                        password: password.text,
                      )),
                    ),

                    const SizedBox(height: 24),
                    Row(children: [
                      Expanded(child: Divider(color: _border)),
                      const Padding(
                        padding: EdgeInsets.symmetric(horizontal: 14),
                        child: Text('or', style: TextStyle(color: _muted, fontSize: 11, fontFamily: _mono)),
                      ),
                      Expanded(child: Divider(color: _border)),
                    ]),
                    const SizedBox(height: 20),

                    // Social
                    Row(children: [
                      Expanded(child: _SocialBtn(
                        icon: const FaIcon(FontAwesomeIcons.google, size: 14),
                        label: 'Google',
                        onTap: _handleGoogleSignIn,
                      )),
                      const SizedBox(width: 12),
                      Expanded(child: _SocialBtn(
                        icon: const FaIcon(FontAwesomeIcons.discord, size: 14),
                        label: 'Discord',
                        onTap: () {},
                      )),
                    ]),
                  ],
                ),
              ),

              const SizedBox(height: 24),

              // Create account
              Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                const Text("Don't have an account? ", style: TextStyle(color: _muted, fontSize: 13)),
                GestureDetector(
                  onTap: () => _run(() => widget.state.api.signup(
                    email: email.text,
                    password: password.text,
                    nickname: nickname.text,
                  )),
                  child: const Text(
                    'Create Account',
                    style: TextStyle(color: TranquilTheme.glowCyan, fontWeight: FontWeight.bold, fontSize: 13),
                  ),
                ),
              ]),

              const SizedBox(height: 20),

              // System status
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 7),
                decoration: BoxDecoration(
                  border: Border.all(color: TranquilTheme.glowCyan.withOpacity(0.3)),
                  borderRadius: BorderRadius.circular(20),
                ),
                child: const Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.circle, size: 7, color: TranquilTheme.glowCyan),
                    SizedBox(width: 8),
                    Text('SYSTEM ONLINE', style: TextStyle(color: TranquilTheme.textBright, fontSize: 10, fontFamily: _mono, letterSpacing: 1.5)),
                  ],
                ),
              ),

              if (status.isNotEmpty) ...[
                const SizedBox(height: 16),
                Text(
                  status,
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: status == 'OK' ? _green : Colors.redAccent,
                    fontSize: 12,
                    fontFamily: _mono,
                  ),
                ),
              ],
              const SizedBox(height: 20),
            ],
          ),
        ),
      ),
    ),
  );
}
}

// ─── Shared widgets ────────────────────────────────────────────────────────────

class _FieldLabel extends StatelessWidget {
  const _FieldLabel(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Text(
    text,
    style: const TextStyle(color: _muted, fontSize: 11, fontFamily: _mono, letterSpacing: 0.5),
  );
}

class _TermField extends StatelessWidget {
  const _TermField({
    required this.controller,
    required this.hint,
    required this.prefixIcon,
    this.obscureText = false,
    this.keyboardType,
    this.suffixIcon,
  });

  final TextEditingController controller;
  final String hint;
  final IconData prefixIcon;
  final bool obscureText;
  final TextInputType? keyboardType;
  final Widget? suffixIcon;

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      obscureText: obscureText,
      keyboardType: keyboardType,
      style: const TextStyle(color: _text, fontSize: 14),
      cursorColor: TranquilTheme.glowCyan,
      decoration: InputDecoration(
        prefixIcon: Icon(prefixIcon, color: TranquilTheme.textMuted, size: 17),
        suffixIcon: suffixIcon,
        hintText: hint,
        hintStyle: const TextStyle(color: TranquilTheme.textMuted, fontSize: 13),
        filled: true,
        fillColor: TranquilTheme.deepWater.withOpacity(0.5),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.3)),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.3)),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide(color: TranquilTheme.glowCyan),
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      ),
    );
  }
}
class _SocialBtn extends StatelessWidget {
  const _SocialBtn({required this.icon, required this.label, required this.onTap});
  final Widget icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        height: 48,
        decoration: BoxDecoration(
          color: TranquilTheme.deepWater.withOpacity(0.5),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: TranquilTheme.glowCyan.withOpacity(0.3)),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            IconTheme(
              data: const IconThemeData(color: TranquilTheme.textMuted, size: 14),
              child: icon,
            ),
            const SizedBox(width: 8),
            Text(label, style: const TextStyle(color: TranquilTheme.textMuted, fontSize: 13)),
          ],
        ),
      ),
    );
  }
}