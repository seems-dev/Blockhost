import 'package:flutter/material.dart';
import '../../state/app_state.dart';

class OnboardingScreen extends StatefulWidget {
  final AppState state;
  final VoidCallback onComplete;

  const OnboardingScreen({
    super.key,
    required this.state,
    required this.onComplete,
  });

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  final PageController _pageController = PageController();
  int _currentPage = 0;

  // Professional Color Palette (Matching Splash Screen)
  static const Color darkBackground = Color(0xFF0F172A);
  static const Color surfaceColor = Color(0xFF1E293B);
  static const Color accentColor = Color(0xFF06B6D4);
  static const Color textBright = Colors.white;
  static const Color textMuted = Colors.white54;

  final List<Map<String, dynamic>> _onboardingData = [
    {
      "icon": Icons.dns_rounded,
      "title": "Welcome to EREX",
      "body": "The premier enterprise hosting solution for Bedrock creators. Scalable, fast, and reliable."
    },
    {
      "icon": Icons.speed_rounded,
      "title": "Powerful Control Panel",
      "body": "Manage your servers with ease. Edit files, switch software, and monitor performance in real-time."
    },
    {
      "icon": Icons.upgrade_rounded,
      "title": "Instant Upgrades",
      "body": "Scale your resources instantly as your community grows. Zero downtime, maximum performance."
    },
  ];

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

  void _onPageChanged(int index) {
    setState(() {
      _currentPage = index;
    });
  }

  Future<void> _completeOnboarding() async {
    await widget.state.completeOnboarding();
    widget.onComplete();
  }

  void _nextPage() {
    _pageController.nextPage(
      duration: const Duration(milliseconds: 400),
      curve: Curves.easeInOutCubic,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment.center,
            radius: 0.8,
            colors: [surfaceColor, darkBackground],
          ),
        ),
        child: SafeArea(
          child: Column(
            children: [
              // Skip Button
              Align(
                alignment: Alignment.topRight,
                child: Padding(
                  padding: const EdgeInsets.only(top: 16.0, right: 16.0),
                  child: TextButton(
                    onPressed: _completeOnboarding,
                    child: const Text(
                      "Skip",
                      style: TextStyle(
                        color: textMuted,
                        fontSize: 14,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                ),
              ),
              
              // Main Content
              Expanded(
                child: PageView.builder(
                  controller: _pageController,
                  onPageChanged: _onPageChanged,
                  itemCount: _onboardingData.length,
                  itemBuilder: (context, index) {
                    return Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 40.0),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          // Icon Container
                          Container(
                            padding: const EdgeInsets.all(28),
                            decoration: BoxDecoration(
                              color: accentColor.withOpacity(0.1),
                              shape: BoxShape.circle,
                              border: Border.all(
                                color: accentColor.withOpacity(0.4),
                                width: 1.5,
                              ),
                              boxShadow: [
                                BoxShadow(
                                  color: accentColor.withOpacity(0.15),
                                  blurRadius: 30,
                                  spreadRadius: 5,
                                ),
                              ],
                            ),
                            child: Icon(
                              _onboardingData[index]["icon"] as IconData,
                              size: 64,
                              color: accentColor,
                            ),
                          ),
                          const SizedBox(height: 48),
                          
                          // Title
                          Text(
                            _onboardingData[index]["title"] as String,
                            textAlign: TextAlign.center,
                            style: const TextStyle(
                              color: textBright,
                              fontSize: 28,
                              fontWeight: FontWeight.w800,
                              letterSpacing: 0.5,
                            ),
                          ),
                          const SizedBox(height: 16),
                          
                          // Body
                          Text(
                            _onboardingData[index]["body"] as String,
                            textAlign: TextAlign.center,
                            style: const TextStyle(
                              color: textMuted,
                              fontSize: 16,
                              height: 1.5,
                              fontWeight: FontWeight.w400,
                            ),
                          ),
                        ],
                      ),
                    );
                  },
                ),
              ),
              
              // Bottom Controls
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 40.0, vertical: 32.0),
                child: Column(
                  children: [
                    // Page Indicators
                    Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: List.generate(
                        _onboardingData.length,
                        (index) => AnimatedContainer(
                          duration: const Duration(milliseconds: 400),
                          curve: Curves.easeInOutCubic,
                          margin: const EdgeInsets.symmetric(horizontal: 4.0),
                          height: 8.0,
                          width: _currentPage == index ? 24.0 : 8.0,
                          decoration: BoxDecoration(
                            color: _currentPage == index
                                ? accentColor
                                : textMuted.withOpacity(0.3),
                            borderRadius: BorderRadius.circular(4.0),
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 40),
                    
                    // Custom Professional Button
                    SizedBox(
                      width: double.infinity,
                      height: 56,
                      child: ElevatedButton(
                        onPressed: _currentPage == _onboardingData.length - 1 
                            ? _completeOnboarding 
                            : _nextPage,
                        style: ElevatedButton.styleFrom(
                          backgroundColor: accentColor,
                          foregroundColor: darkBackground,
                          elevation: 0,
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(12.0),
                          ),
                        ),
                        child: Text(
                          _currentPage == _onboardingData.length - 1 
                              ? "Get Started" 
                              : "Next",
                          style: const TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.bold,
                            letterSpacing: 0.5,
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}