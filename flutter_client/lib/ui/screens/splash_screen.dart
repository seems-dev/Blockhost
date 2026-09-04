import 'package:flutter/material.dart';
// We will build a new theme file later, but for now, we define professional colors here.
// import '../theme/app_theme.dart'; 

class SplashScreen extends StatefulWidget {
  const SplashScreen({super.key});

  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;
  late Animation<double> _fadeAnimation;
  late Animation<double> _scaleAnimation;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    );

    _fadeAnimation = CurvedAnimation(
      parent: _controller,
      curve: Curves.easeIn,
    );

    _scaleAnimation = Tween<double>(begin: 0.8, end: 1.0).animate(
      CurvedAnimation(parent: _controller, curve: Curves.easeOutBack),
    );

    _controller.forward();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // Professional Color Palette
    const Color darkBackground = Color(0xFF0F172A); // Deep Slate
    const Color accentColor = Color(0xFF06B6D4); // Modern Cyan
    const Color surfaceColor = Color(0xFF1E293B); // Lighter Slate for contrast

    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment.center,
            radius: 0.8,
            colors: [
              surfaceColor,
              darkBackground,
            ],
          ),
        ),
        child: Center(
          child: FadeTransition(
            opacity: _fadeAnimation,
            child: ScaleTransition(
              scale: _scaleAnimation,
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  // Modern Logo Container
                  Container(
                    padding: const EdgeInsets.all(20),
                    decoration: BoxDecoration(
                      color: accentColor.withOpacity(0.1),
                      borderRadius: BorderRadius.circular(24),
                      border: Border.all(
                        color: accentColor.withOpacity(0.5),
                        width: 1.5,
                      ),
                      boxShadow: [
                        BoxShadow(
                          color: accentColor.withOpacity(0.2),
                          blurRadius: 30,
                          spreadRadius: 5,
                        ),
                      ],
                    ),
                    child: const Icon(
                      Icons.dns_rounded, // Placeholder for your logo
                      color: accentColor,
                      size: 56,
                    ),
                  ),
                  const SizedBox(height: 32),
                  
                  // Brand Name
                  const Text(
                    'EREX',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 42,
                      fontWeight: FontWeight.w800,
                      letterSpacing: 4,
                      // fontFamily: 'Inter', // Add a professional font like Inter or Poppins in pubspec.yaml later
                    ),
                  ),
                  const SizedBox(height: 8),
                  
                  // Subtitle
                  const Text(
                    'MINECRAFT HOSTING',
                    style: TextStyle(
                      color: Colors.white54,
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                      letterSpacing: 6,
                    ),
                  ),
                  const SizedBox(height: 48),
                  
                  // Sleek Loading Indicator
                  SizedBox(
                    width: 40,
                    height: 40,
                    child: CircularProgressIndicator(
                      color: accentColor,
                      strokeWidth: 2.5,
                      backgroundColor: Colors.white.withOpacity(0.1),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}