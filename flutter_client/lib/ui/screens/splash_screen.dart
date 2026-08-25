import 'package:flutter/material.dart';
import '../theme/tranquil_theme.dart';

class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      backgroundColor: Color(0xFF0A0A0A),
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text(
              'EREX',
              style: TextStyle(
                color: TranquilTheme.glowCyan,
                fontSize: 48,
                fontWeight: FontWeight.w900,
                fontFamily: 'monospace',
                letterSpacing: 10,
              ),
            ),
            SizedBox(height: 24),
            CircularProgressIndicator(
              color: TranquilTheme.glowCyan,
            ),
          ],
        ),
      ),
    );
  }
}
