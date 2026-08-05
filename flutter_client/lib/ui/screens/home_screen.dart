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
          child: Image.asset('assets/bg_tranquil_pond.png', fit: BoxFit.cover),
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
    return GlassCard(
      margin: const EdgeInsets.only(left: 16, right: 16, bottom: 16),
      padding: EdgeInsets.zero,
      child: SizedBox(
        height: 68,
        child: Row(
          children: [
            _NavItem(icon: Icons.dashboard_rounded,         label: 'Dashboard', index: 0, selectedIndex: selectedIndex, onTap: onTap),
            _NavItem(icon: Icons.add_rounded,               label: 'Create',    index: 1, selectedIndex: selectedIndex, onTap: onTap, isCreate: true),
            _NavItem(icon: Icons.workspace_premium_rounded, label: 'Plans',     index: 2, selectedIndex: selectedIndex, onTap: onTap),
            _NavItem(icon: Icons.settings_rounded,          label: 'Settings',  index: 3, selectedIndex: selectedIndex, onTap: onTap),
          ],
        ),
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
    this.isCreate = false,
  });

  final IconData icon;
  final String label;
  final int index;
  final int selectedIndex;
  final ValueChanged<int> onTap;
  final bool isCreate;

  bool get _selected => selectedIndex == index;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: GestureDetector(
        onTap: () {
          AudioService.playClick();
          onTap(index);
        },
        behavior: HitTestBehavior.opaque,
        child: SizedBox(
          height: 68,
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              if (isCreate && _selected)
                Container(
                  width: 44, height: 44,
                  decoration: BoxDecoration(
                    color: TranquilTheme.glowCyan,
                    borderRadius: BorderRadius.circular(22),
                    boxShadow: [BoxShadow(color: TranquilTheme.glowCyan.withOpacity(0.3), blurRadius: 8, spreadRadius: 1)],
                  ),
                  child: Icon(icon, color: TranquilTheme.deepWater, size: 22),
                )
              else if (isCreate)
                Container(
                  width: 44, height: 44,
                  decoration: BoxDecoration(
                    border: Border.all(color: TranquilTheme.glowCyan.withOpacity(0.3)),
                    borderRadius: BorderRadius.circular(22),
                  ),
                  child: Icon(icon, color: TranquilTheme.textBright, size: 22),
                )
              else
                Icon(icon, color: _selected ? TranquilTheme.glowCyan : TranquilTheme.textMuted, size: 22),
              if (!isCreate) ...[
                const SizedBox(height: 4),
                Text(
                  label,
                  style: TextStyle(
                    color: _selected ? TranquilTheme.glowCyan : TranquilTheme.textMuted,
                    fontSize: 10,
                    fontFamily: 'monospace',
                    fontWeight: _selected ? FontWeight.w600 : FontWeight.normal,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}