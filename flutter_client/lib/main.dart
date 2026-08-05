import 'package:flutter/material.dart';

import 'state/app_state.dart';
import 'ui/screens/home_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const BlockHostApp());
}

class BlockHostApp extends StatefulWidget {
  const BlockHostApp({super.key});

  @override
  State<BlockHostApp> createState() => _BlockHostAppState();
}

class _BlockHostAppState extends State<BlockHostApp> {
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
    });
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'BlockHost',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.indigo),
        textTheme: Typography.material2021().black,
        primaryTextTheme: Typography.material2021().black,
        scaffoldBackgroundColor: Colors.grey.shade100,
        visualDensity: VisualDensity.adaptivePlatformDensity,
      ),
      home: _initialized ? HomeScreen(state: state) : const Scaffold(backgroundColor: Color(0xFF0A0A0A), body: Center(child: CircularProgressIndicator(color: Color(0xFF00FF6A)))),
    );
  }
}

