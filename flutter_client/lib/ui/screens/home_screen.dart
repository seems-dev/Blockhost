import 'package:flutter/material.dart';

import '../../services/audio_service.dart';
import '../../state/app_state.dart';
import '../theme/tranquil_theme.dart';
import '../widgets/glass_card.dart';
import 'login_screen.dart';
import 'dashboard_screen.dart';
import 'create_server_screen.dart';
import 'plans_screen.dart';
import 'settings_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.state});
  final AppState state;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int index = 0;

  @override
  void initState() {
    super.initState();
    AudioService.init();
  }

  @override
  void dispose() {
    super.dispose();
  }

  void _goToDashboard() => setState(() => index = 0);

  @override
  Widget build(BuildContext context) {
    final hasToken = (widget.state.accessToken ?? '').isNotEmpty;

    final pages = [
      hasToken
          ? DashboardScreen(state: widget.state)
          : LoginScreen(state: widget.state, onLoggedIn: () => setState(() => index = 0)),
      CreateServerScreen(state: widget.state, onCreated: _goToDashboard),
      PlansScreen(state: widget.state, onPlanChanged: () => setState(() {})),
      SettingsScreen(state: widget.state, onAuthChanged: () => setState(() => index = 0)),
    ];

    return Stack(
      children: [
        // ── Background image ─────────────────────────────────────────────
        Positioned.fill(
          child: Image.asset('assets/bg_tranquil_pond.png', fit: BoxFit.cover, color: const Color(0xFF0F0020).withOpacity(0.8), colorBlendMode: BlendMode.darken),
        ),

        // ── App scaffold ─────────────────────────────────────────────────
        Scaffold(
          backgroundColor: Colors.transparent,
          extendBody: true,
          body: AnimatedSwitcher(
            duration: const Duration(milliseconds: 250),
            child: KeyedSubtree(key: ValueKey(index), child: pages[index]),
          ),
          bottomNavigationBar: _BottomNav(
            selectedIndex: index,
            onTap: (i) => setState(() => index = i),
          ),
        ),
      ],
    );
  }
}

class _BottomNav extends StatelessWidget {
  const _BottomNav({required this.selectedIndex, required this.onTap});
  final int selectedIndex;
  final ValueChanged<int> onTap;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      decoration: BoxDecoration(
        color: const Color(0xFF161622),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: Colors.white.withOpacity(0.05)),
      ),
      height: 64,
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
        children: [
          _NavItem(icon: Icons.view_agenda_rounded, label: 'Servers',  index: 0, selectedIndex: selectedIndex, onTap: onTap),
          _NavItem(icon: Icons.terminal_rounded,    label: 'Console',  index: 1, selectedIndex: selectedIndex, onTap: onTap),
          _NavItem(icon: Icons.folder_outlined,     label: 'Files',    index: 2, selectedIndex: selectedIndex, onTap: onTap),
          _NavItem(icon: Icons.settings_outlined,   label: 'Settings', index: 3, selectedIndex: selectedIndex, onTap: onTap),
        ],
      ),
    );
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem({
    required this.icon,
    required this.label,
    required this.index,
    required this.selectedIndex,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final int index;
  final int selectedIndex;
  final ValueChanged<int> onTap;

  bool get _selected => selectedIndex == index;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () {
        AudioService.playClick();
        onTap(index);
      },
      behavior: HitTestBehavior.opaque,
      child: Container(
        width: 72,
        margin: const EdgeInsets.symmetric(vertical: 8),
        decoration: BoxDecoration(
          color: _selected ? const Color(0xFF06B6D4).withOpacity(0.15) : Colors.transparent,
          borderRadius: BorderRadius.circular(16),
          border: _selected ? Border.all(color: const Color(0xFF06B6D4).withOpacity(0.3)) : null,
        ),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              icon,
              color: _selected ? const Color(0xFF06B6D4) : const Color(0xFF8A8A93),
              size: 20,
            ),
            const SizedBox(height: 4),
            Text(
              label,
              style: TextStyle(
                color: _selected ? const Color(0xFF06B6D4) : const Color(0xFF8A8A93),
                fontSize: 10,
                fontWeight: FontWeight.bold,
                letterSpacing: 0.5,
              ),
            ),
          ],
        ),
      ),
    );
  }
}