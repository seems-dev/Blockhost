import 'package:flutter/material.dart';

import 'state/app_state.dart';
import 'ui/screens/home_screen.dart';
import 'ui/screens/splash_screen.dart';
import 'ui/screens/onboarding_screen.dart';
import 'ui/theme/tranquil_theme.dart';
import 'services/push_notification_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await PushNotificationService.init();
  runApp(const ErexApp());
}

class ErexApp extends StatefulWidget {
  const ErexApp({super.key});

  @override
  State<ErexApp> createState() => _ErexAppState();
}

class _ErexAppState extends State<ErexApp> {
  final AppState state = AppState();

  bool _initialized = false;

  @override
  void initState() {
    super.initState();
    state.init().then((_) {
      if (mounted) {
        setState(() {
          _initialized = true;
        });
      }
    }).catchError((error, stackTrace) {
      debugPrint('Initialization error: $error\n$stackTrace');
      // Even if init fails (e.g. plugin error, network error), 
      // we must transition away from the splash screen.
      if (mounted) {
        setState(() {
          _initialized = true;
        });
      }
    });
  }

  Widget _buildHome() {
    if (!_initialized) {
      return const SplashScreen();
    }
    if (!state.hasSeenOnboarding) {
      return OnboardingScreen(
        state: state,
        onComplete: () {
          setState(() {}); 
        },
      );
    }
    return HomeScreen(state: state);
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Erex',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: TranquilTheme.glowCyan,
          brightness: Brightness.dark,
        ),
        scaffoldBackgroundColor: const Color(0xFF0A0A0A),
        textTheme: Typography.material2021().white,
        primaryTextTheme: Typography.material2021().white,
        visualDensity: VisualDensity.adaptivePlatformDensity,
      ),
      home: _buildHome(),
    );
  }
}

