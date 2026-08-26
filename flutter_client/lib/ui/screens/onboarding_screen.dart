import 'package:flutter/material.dart';
import '../../state/app_state.dart';
import '../theme/tranquil_theme.dart';
import '../widgets/glowing_button.dart';

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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0A0A0A),
      body: SafeArea(
        child: Column(
          children: [
            Align(
              alignment: Alignment.topRight,
              child: TextButton(
                onPressed: _completeOnboarding,
                child: const Text(
                  "Skip",
                  style: TextStyle(
                    color: TranquilTheme.textMuted,
                    fontFamily: 'monospace',
                  ),
                ),
              ),
            ),
            Expanded(
              child: PageView.builder(
                controller: _pageController,
                onPageChanged: _onPageChanged,
                itemCount: _onboardingData.length,
                itemBuilder: (context, index) {
                  return Padding(
                    padding: const EdgeInsets.all(40.0),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(
                          _onboardingData[index]["icon"],
                          size: 100,
                          color: TranquilTheme.glowCyan,
                        ),
                        const SizedBox(height: 60),
                        Text(
                          _onboardingData[index]["title"],
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                            color: TranquilTheme.textBright,
                            fontSize: 28,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(height: 20),
                        Text(
                          _onboardingData[index]["body"],
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                            color: TranquilTheme.textMuted,
                            fontSize: 16,
                            height: 1.5,
                          ),
                        ),
                      ],
                    ),
                  );
                },
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(40.0),
              child: Column(
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: List.generate(
                      _onboardingData.length,
                      (index) => AnimatedContainer(
                        duration: const Duration(milliseconds: 300),
                        margin: const EdgeInsets.symmetric(horizontal: 4.0),
                        height: 8.0,
                        width: _currentPage == index ? 24.0 : 8.0,
                        decoration: BoxDecoration(
                          color: _currentPage == index
                              ? TranquilTheme.glowCyan
                              : TranquilTheme.textMuted.withOpacity(0.5),
                          borderRadius: BorderRadius.circular(4.0),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 40),
                  if (_currentPage == _onboardingData.length - 1)
                    GlowingButton(
                      label: "Get Started  →",
                      onTap: _completeOnboarding,
                    )
                  else
                    GlowingButton(
                      label: "Next  →",
                      onTap: () {
                        _pageController.nextPage(
                          duration: const Duration(milliseconds: 300),
                          curve: Curves.easeIn,
                        );
                      },
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
