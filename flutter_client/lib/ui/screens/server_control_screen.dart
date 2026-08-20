import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../api/blockhost_api.dart';
import '../../state/app_state.dart';
import '../theme/tranquil_theme.dart';
import '../widgets/glass_card.dart';
import 'bans_screen.dart';
import 'mod_screen.dart';
import 'server_console_screen.dart';
import 'server_files_screen.dart';
import 'server_properties_screen.dart';
import 'software_switch_screen.dart';

// ─── Design tokens (using actual TranquilTheme properties) ─────────────────────
const _mono = 'monospace';

// Additional colors not in TranquilTheme
const _errorRed = Color(0xFFFF6B6B);
const _warningAmber = Color(0xFFFFD93D);
const _surfaceGlass = Color(0x80051923); // Same as create server screen
const _borderGlow = Color(0x4D64FFDA);   // Same as create server screen
const _textDim = Color(0xFFB0BEC5);

class ServerControlScreen extends StatefulWidget {
  const ServerControlScreen({
    super.key,
    required this.state,
    required this.serverId,
    required this.worldName,
    required this.isRunning,
    this.onlinePlayers = const [],
  });

  final AppState state;
  final String serverId;
  final String worldName;
  final bool isRunning;
  final List<String> onlinePlayers;

  @override
  State<ServerControlScreen> createState() => _ServerControlScreenState();
}

class _ServerControlScreenState extends State<ServerControlScreen> {
  final _playerCtrl = TextEditingController();
  final _xCtrl      = TextEditingController(text: '0');
  final _yCtrl      = TextEditingController(text: '64');
  final _zCtrl      = TextEditingController(text: '0');
  final _sayCtrl    = TextEditingController();

  bool         _busy     = false;
  String?      _status;
  bool         _statusOk = true;
  List<String> _blocklist = [];
  String       _gamemode  = 'survival';

  @override
  void initState() {
    super.initState();
    if (widget.onlinePlayers.isNotEmpty) {
      _playerCtrl.text = widget.onlinePlayers.first;
    }
    _loadBlocklist();
  }

  @override
  void dispose() {
    _playerCtrl.dispose();
    _xCtrl.dispose();
    _yCtrl.dispose();
    _zCtrl.dispose();
    _sayCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadBlocklist() async {
    try {
      final list = await widget.state.api.getBlocklist(widget.serverId);
      if (mounted) setState(() => _blocklist = list);
    } catch (_) {}
  }

  Future<void> _run(
    Future<Map<String, dynamic>> Function() action,
    String successMsg, {
    bool requiresPlayer = false,
  }) async {
    if (requiresPlayer && _player.isEmpty) {
      _setStatus('Please specify a Target Player first', ok: false);
      return;
    }
    if (!widget.isRunning) {
      _setStatus('Start the server first', ok: false);
      return;
    }
    setState(() { _busy = true; _status = null; });
    try {
      await action();
      if (mounted) _setStatus(successMsg, ok: true);
    } on ApiException catch (e) {
      if (mounted) _setStatus(e.message, ok: false);
    } catch (e) {
      if (mounted) _setStatus(e.toString(), ok: false);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _setStatus(String msg, {required bool ok}) =>
      setState(() { _status = msg; _statusOk = ok; });

  String get _player => _playerCtrl.text.trim();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: Stack(
        children: [
          // ── Background (same as create server screen) ──────────────────────
          Container(
            decoration: const BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [Color(0xFF0A0F1A), Color(0xFF061520), Color(0xFF021B2A)],
              ),
            ),
          ),
          
          // ── Glass blur overlay ─────────────────────────────────────────────
          ClipRect(
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
              child: Container(color: Colors.transparent),
            ),
          ),
          
          SafeArea(
            child: Column(
              children: [
                // ── Header ─────────────────────────────────────────────────────
                _buildGlassHeader(),
                
                // ── Main Content ───────────────────────────────────────────────
                Expanded(
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.all(20),
                    child: Column(
                      children: [
                        if (_status != null) ...[
                          _StatusBanner(message: _status!, ok: _statusOk),
                          const SizedBox(height: 16),
                        ],
                        
                        // Quick action buttons (2×2 grid)
                        Column(children: [
                          Row(children: [
                            Expanded(child: _ConsoleButton(
                              enabled: widget.isRunning,
                              onTap: () => Navigator.push(
                                context,
                                MaterialPageRoute(
                                  builder: (_) => ServerConsoleScreen(
                                    state: widget.state,
                                    serverId: widget.serverId,
                                    worldName: widget.worldName,
                                  ),
                                ),
                              ),
                            )),
                            const SizedBox(width: 12),
                            Expanded(child: _FilesButton(
                              onTap: () => Navigator.push(
                                context,
                                MaterialPageRoute(
                                  builder: (_) => ServerFilesScreen(
                                    state: widget.state,
                                    serverId: widget.serverId,
                                    worldName: widget.worldName,
                                  ),
                                ),
                              ),
                            )),
                          ]),
                          const SizedBox(height: 12),
                          Row(children: [
                            Expanded(child: _ModsButton(
                              onTap: () => Navigator.push(
                                context,
                                MaterialPageRoute(
                                  builder: (_) => ModsScreen(
                                    serverId: widget.serverId,
                                    state: widget.state,
                                  ),
                                ),
                              ),
                            )),
                            const SizedBox(width: 12),
                            Expanded(child: _BansButton(
                              onTap: () => Navigator.push(
                                context,
                                MaterialPageRoute(
                                  builder: (_) => BansScreen(
                                    state: widget.state,
                                    serverId: widget.serverId,
                                    worldName: widget.worldName,
                                  ),
                                ),
                              ),
                            )),
                          ]),
                          const SizedBox(height: 12),
                          Row(children: [
                            Expanded(child: _PropertiesButton(
                              onTap: () => Navigator.push(
                                context,
                                MaterialPageRoute(
                                  builder: (_) => ServerPropertiesScreen(
                                    state: widget.state,
                                    serverId: widget.serverId,
                                    flavor: widget.flavor,
                                  ),
                                ),
                              ),
                            )),
                            const SizedBox(width: 12),
                            Expanded(child: const SizedBox.shrink()), // Empty placeholder for symmetry
                          ]),
                        ]),
                        const SizedBox(height: 20),
                        
                        // Target Player Section
                        _GlassSection(
                          title: 'TARGET PLAYER',
                          icon: Icons.person_outline_rounded,
                          child: _buildTargetPlayer(),
                        ),
                        const SizedBox(height: 16),
                        
                        // Player Actions Section
                        _GlassSection(
                          title: 'PLAYER ACTIONS',
                          icon: Icons.games_rounded,
                          child: _buildPlayerActions(),
                        ),
                        const SizedBox(height: 16),
                        
                        // Gamemode Section
                        _GlassSection(
                          title: 'GAMEMODE',
                          icon: Icons.sports_esports_rounded,
                          child: _buildGamemode(),
                        ),
                        const SizedBox(height: 16),
                        
                        // Teleportation Section
                        _GlassSection(
                          title: 'TELEPORTATION',
                          icon: Icons.navigation_rounded,
                          child: _buildTeleport(),
                        ),
                        const SizedBox(height: 16),
                        
                        // World Controls Section
                        _GlassSection(
                          title: 'WORLD CONTROLS',
                          icon: Icons.public_rounded,
                          child: _buildWorld(),
                        ),
                        const SizedBox(height: 16),
                        
                        // Blocklist Section
                        if (_blocklist.isNotEmpty)
                          _GlassSection(
                            title: 'BANNED PLAYERS',
                            icon: Icons.block_rounded,
                            child: _buildBlocklist(),
                          ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
          
          // Loading overlay
          if (_busy)
            Container(
              color: Colors.black54,
              child: const Center(
                child: CircularProgressIndicator(
                  color: TranquilTheme.glowCyan, 
                  strokeWidth: 2.5,
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildGlassHeader() {
    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
        child: Container(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 14),
          decoration: BoxDecoration(
            color: TranquilTheme.deepWater.withOpacity(0.4),
            border: Border(bottom: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.2))),
          ),
          child: Row(
            children: [
              // Back button
              GestureDetector(
                onTap: () => Navigator.maybePop(context),
                child: Container(
                  width: 34, height: 34,
                  decoration: BoxDecoration(
                    color: TranquilTheme.glowCyan.withOpacity(0.1),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: TranquilTheme.glowCyan.withOpacity(0.3)),
                  ),
                  child: Icon(Icons.arrow_back_ios_new_rounded, size: 15, color: TranquilTheme.glowCyan),
                ),
              ),
              const SizedBox(width: 12),
              // Server info
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      widget.worldName,
                      style: const TextStyle(color: Colors.white, fontSize: 17, fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 2),
                    Row(
                      children: [
                        Container(
                          width: 6, height: 6,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: widget.isRunning ? TranquilTheme.glowCyan : _errorRed,
                          ),
                        ),
                        const SizedBox(width: 6),
                        Text(
                          widget.isRunning ? 'Running' : 'Stopped',
                          style: TextStyle(color: _textDim, fontSize: 11, fontFamily: _mono),
                        ),
                        const SizedBox(width: 12),
                        Text(
                          '${widget.onlinePlayers.length} players online',
                          style: TextStyle(color: _textDim, fontSize: 11, fontFamily: _mono),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              // Status badge
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                decoration: BoxDecoration(
                  color: widget.isRunning 
                      ? TranquilTheme.glowCyan.withOpacity(0.1) 
                      : _errorRed.withOpacity(0.1),
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(
                    color: widget.isRunning 
                        ? TranquilTheme.glowCyan.withOpacity(0.3) 
                        : _errorRed.withOpacity(0.3),
                  ),
                ),
                child: Text(
                  widget.isRunning ? 'ACTIVE' : 'OFFLINE',
                  style: TextStyle(
                    color: widget.isRunning ? TranquilTheme.glowCyan : _errorRed,
                    fontSize: 9,
                    fontWeight: FontWeight.bold,
                    letterSpacing: 1.2,
                    fontFamily: _mono,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildTargetPlayer() => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _GlassTextField(
            controller: _playerCtrl, 
            hint: 'Enter player name...', 
            icon: Icons.search_rounded,
          ),
          if (widget.onlinePlayers.isNotEmpty) ...[
            const SizedBox(height: 12),
            Wrap(
              spacing: 8, runSpacing: 8,
              children: widget.onlinePlayers.map((p) => _GlassChip(
                label: p,
                icon: Icons.person_rounded,
                onTap: () => setState(() => _playerCtrl.text = p),
              )).toList(),
            ),
          ],
          if (!widget.isRunning)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: _InfoHint(
                icon: Icons.info_outline_rounded,
                text: 'Server must be running to execute player commands',
                color: _warningAmber,
              ),
            ),
        ],
      );

  Widget _buildPlayerActions() => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(children: [
            Expanded(child: _GlassActionTile(
              icon: Icons.military_tech_rounded,
              label: 'Grant OP',
              onTap: () => _run(
                () => widget.state.api.opPlayer(widget.serverId, player: _player),
                'OP granted', requiresPlayer: true,
              ),
            )),
            const SizedBox(width: 10),
            Expanded(child: _GlassActionTile(
              icon: Icons.shield_outlined,
              label: 'Remove OP',
              onTap: () => _run(
                () => widget.state.api.opPlayer(widget.serverId, player: _player, grant: false),
                'OP removed', requiresPlayer: true,
              ),
            )),
          ]),
          const SizedBox(height: 10),
          Row(children: [
            Expanded(child: _GlassActionTile(
              icon: Icons.backpack_outlined,
              label: 'Clear Inv',
              onTap: () => _run(
                () => widget.state.api.clearInventory(widget.serverId, player: _player),
                'Inventory cleared', requiresPlayer: true,
              ),
            )),
            const SizedBox(width: 10),
            Expanded(child: _GlassActionTile(
              icon: Icons.logout_rounded,
              label: 'Kick',
              color: _warningAmber,
              onTap: () => _run(
                () => widget.state.api.kickPlayer(widget.serverId, player: _player),
                'Player kicked', requiresPlayer: true,
              ),
            )),
          ]),
          const SizedBox(height: 12),
          _GlassPrimaryButton(
            icon: Icons.block_rounded,
            label: 'Open Ban Manager',
            color: _errorRed,
            onTap: _busy
                ? null
                : () {
                    Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (_) => BansScreen(
                          state: widget.state,
                          serverId: widget.serverId,
                          worldName: widget.worldName,
                        ),
                      ),
                    );
                  },
          ),
        ],
      );

  Widget _buildGamemode() {
    const modes = ['survival', 'creative', 'adventure', 'spectator'];
    return Wrap(
      spacing: 8, runSpacing: 8,
      children: modes.map((m) => _GlassModeChip(
        label: m[0].toUpperCase() + m.substring(1),
        selected: _gamemode == m,
        onTap: () async {
          setState(() => _gamemode = m);
          await _run(
            () => widget.state.api.setGamemode(widget.serverId, player: _player, mode: m),
            'Gamemode set to $m', requiresPlayer: true,
          );
        },
      )).toList(),
    );
  }

  Widget _buildTeleport() => Column(
        children: [
          Row(children: [
            Expanded(child: _GlassCoordField(controller: _xCtrl, label: 'X')),
            const SizedBox(width: 8),
            Expanded(child: _GlassCoordField(controller: _yCtrl, label: 'Y')),
            const SizedBox(width: 8),
            Expanded(child: _GlassCoordField(controller: _zCtrl, label: 'Z')),
          ]),
          const SizedBox(height: 14),
          _GlassPrimaryButton(
            icon: Icons.navigation_rounded,
            label: 'Teleport Player',
            color: TranquilTheme.glowCyan,
            onTap: _busy
                ? null
                : () {
                    final x = double.tryParse(_xCtrl.text.trim());
                    final y = double.tryParse(_yCtrl.text.trim());
                    final z = double.tryParse(_zCtrl.text.trim());
                    if (x == null || y == null || z == null) {
                      _setStatus('Enter valid coordinates', ok: false);
                      return;
                    }
                    _run(
                      () => widget.state.api.teleportPlayer(widget.serverId, player: _player, x: x, y: y, z: z),
                      'Teleport command sent', requiresPlayer: true,
                    );
                  },
          ),
        ],
      );

  Widget _buildWorld() => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const _GlassSubLabel('TIME'),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8, runSpacing: 8,
            children: ['day', 'noon', 'sunset', 'night', 'midnight'].map((t) => _GlassSmallChip(
              label: t[0].toUpperCase() + t.substring(1),
              onTap: () => _run(
                () => widget.state.api.setTime(widget.serverId, value: t),
                'Time set to $t',
              ),
            )).toList(),
          ),
          const SizedBox(height: 20),
          const _GlassSubLabel('WEATHER'),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8, runSpacing: 8,
            children: [
              _GlassSmallChip(label: 'Clear',   icon: Icons.wb_sunny_rounded,   onTap: () => _run(() => widget.state.api.setWeather(widget.serverId, weather: 'clear'),   'Weather: clear')),
              _GlassSmallChip(label: 'Rain',    icon: Icons.water_drop_rounded,  onTap: () => _run(() => widget.state.api.setWeather(widget.serverId, weather: 'rain'),    'Weather: rain')),
              _GlassSmallChip(label: 'Thunder', icon: Icons.thunderstorm_rounded, onTap: () => _run(() => widget.state.api.setWeather(widget.serverId, weather: 'thunder'), 'Weather: thunder')),
            ],
          ),
          const SizedBox(height: 20),
          _GlassTextField(controller: _sayCtrl, hint: 'Broadcast message', icon: Icons.campaign_rounded),
          const SizedBox(height: 12),
          _GlassPrimaryButton(
            icon: Icons.campaign_rounded,
            label: 'Say to Server',
            color: TranquilTheme.textMuted,
            onTap: _busy
                ? null
                : () => _run(
                      () => widget.state.api.sayMessage(widget.serverId, message: _sayCtrl.text.trim()),
                      'Message broadcast',
                    ),
          ),
        ],
      );

  Widget _buildBlocklist() => Wrap(
        spacing: 8, runSpacing: 8,
        children: _blocklist.map((p) => Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            color: _errorRed.withOpacity(0.1),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: _errorRed.withOpacity(0.3)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.block_rounded, size: 14, color: _errorRed),
              const SizedBox(width: 6),
              Text(p, style: TextStyle(color: Colors.white, fontSize: 12, fontFamily: _mono)),
            ],
          ),
        )).toList(),
      );
}

// ─── Glassmorphic Components ───────────────────────────────────────────────────

class _GlassSection extends StatelessWidget {
  const _GlassSection({
    required this.title,
    required this.icon,
    required this.child,
  });
  final String title;
  final IconData icon;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: _surfaceGlass,
        border: Border.all(color: _borderGlow),
        borderRadius: BorderRadius.circular(16),
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(16),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 4, sigmaY: 4),
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(icon, color: TranquilTheme.glowCyan, size: 16),
                    const SizedBox(width: 8),
                    Text(
                      title,
                      style: TextStyle(
                        color: TranquilTheme.glowCyan,
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                        letterSpacing: 1.5,
                        fontFamily: _mono,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 14),
                child,
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _GlassTextField extends StatelessWidget {
  const _GlassTextField({
    required this.controller,
    required this.hint,
    this.icon,
    this.obscure = false,
  });
  final TextEditingController controller;
  final String hint;
  final IconData? icon;
  final bool obscure;

  @override
  Widget build(BuildContext context) => TextField(
        controller: controller,
        obscureText: obscure,
        style: TextStyle(color: Colors.white, fontSize: 13, fontFamily: _mono),
        decoration: InputDecoration(
          hintText: hint,
          hintStyle: TextStyle(color: TranquilTheme.textMuted, fontSize: 12, fontFamily: _mono),
          prefixIcon: icon != null ? Icon(icon, color: TranquilTheme.textMuted, size: 18) : null,
          filled: true,
          fillColor: TranquilTheme.deepWater.withOpacity(0.3),
          contentPadding: const EdgeInsets.symmetric(vertical: 14, horizontal: 16),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(10),
            borderSide: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.3)),
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(10),
            borderSide: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.3)),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(10),
            borderSide: const BorderSide(color: TranquilTheme.glowCyan),
          ),
        ),
      );
}

class _GlassActionTile extends StatelessWidget {
  const _GlassActionTile({
    required this.icon,
    required this.label,
    required this.onTap,
    this.color = TranquilTheme.glowCyan,
  });
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final Color color;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 12),
          decoration: BoxDecoration(
            color: color.withOpacity(0.08),
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: color.withOpacity(0.25)),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(icon, size: 16, color: color),
              const SizedBox(width: 8),
              Text(
                label,
                style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600, fontFamily: _mono),
              ),
            ],
          ),
        ),
      );
}

class _GlassPrimaryButton extends StatefulWidget {
  const _GlassPrimaryButton({
    required this.icon,
    required this.label,
    required this.color,
    this.onTap,
  });
  final IconData icon;
  final String label;
  final Color color;
  final VoidCallback? onTap;

  @override
  State<_GlassPrimaryButton> createState() => _GlassPrimaryButtonState();
}

class _GlassPrimaryButtonState extends State<_GlassPrimaryButton> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final disabled = widget.onTap == null;
    final bg = disabled ? widget.color.withOpacity(0.2) : widget.color.withOpacity(0.15);

    return GestureDetector(
      onTapDown: disabled ? null : (_) => setState(() => _pressed = true),
      onTapUp: disabled ? null : (_) => setState(() => _pressed = false),
      onTapCancel: disabled ? null : () => setState(() => _pressed = false),
      onTap: widget.onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 80),
        transform: Matrix4.identity()..translate(0.0, _pressed ? 2.0 : 0.0),
        padding: const EdgeInsets.symmetric(vertical: 13),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: widget.color.withOpacity(0.3)),
          boxShadow: _pressed || disabled
              ? []
              : [BoxShadow(color: widget.color.withOpacity(0.2), offset: const Offset(0, 4), blurRadius: 12)],
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(widget.icon, size: 17, color: widget.color),
            const SizedBox(width: 10),
            Text(
              widget.label,
              style: TextStyle(color: widget.color, fontSize: 13, fontWeight: FontWeight.w700, fontFamily: _mono),
            ),
          ],
        ),
      ),
    );
  }
}

class _GlassChip extends StatelessWidget {
  const _GlassChip({required this.label, required this.icon, required this.onTap});
  final String label;
  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          decoration: BoxDecoration(
            color: TranquilTheme.glowCyan.withOpacity(0.1),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: TranquilTheme.glowCyan.withOpacity(0.3)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 14, color: TranquilTheme.glowCyan),
              const SizedBox(width: 6),
              Text(label, style: TextStyle(color: TranquilTheme.glowCyan, fontSize: 12, fontWeight: FontWeight.w600, fontFamily: _mono)),
            ],
          ),
        ),
      );
}

class _GlassModeChip extends StatelessWidget {
  const _GlassModeChip({
    required this.label,
    required this.selected,
    required this.onTap,
  });
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          decoration: BoxDecoration(
            color: selected ? TranquilTheme.glowCyan.withOpacity(0.15) : Colors.transparent,
            borderRadius: BorderRadius.circular(20),
            border: Border.all(
              color: selected ? TranquilTheme.glowCyan : _borderGlow,
              width: selected ? 1.5 : 1,
            ),
          ),
          child: Text(
            label,
            style: TextStyle(
              color: selected ? TranquilTheme.glowCyan : _textDim,
              fontSize: 12,
              fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
              fontFamily: _mono,
            ),
          ),
        ),
      );
}

class _GlassSmallChip extends StatelessWidget {
  const _GlassSmallChip({
    required this.label,
    required this.onTap,
    this.icon,
  });
  final String label;
  final VoidCallback onTap;
  final IconData? icon;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          decoration: BoxDecoration(
            color: TranquilTheme.glowCyan.withOpacity(0.08),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: TranquilTheme.glowCyan.withOpacity(0.25)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (icon != null) ...[Icon(icon, size: 14, color: TranquilTheme.glowCyan), const SizedBox(width: 6)],
              Text(label, style: TextStyle(color: TranquilTheme.glowCyan, fontSize: 12, fontWeight: FontWeight.w600, fontFamily: _mono)),
            ],
          ),
        ),
      );
}

class _GlassCoordField extends StatelessWidget {
  const _GlassCoordField({required this.controller, required this.label});
  final TextEditingController controller;
  final String label;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            label,
            textAlign: TextAlign.center,
            style: TextStyle(color: TranquilTheme.textMuted, fontSize: 10, fontWeight: FontWeight.w600, letterSpacing: 1, fontFamily: _mono),
          ),
          const SizedBox(height: 6),
          TextField(
            controller: controller,
            textAlign: TextAlign.center,
            keyboardType: const TextInputType.numberWithOptions(signed: true, decimal: true),
            inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'^-?\d*\.?\d*'))],
            style: TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w700, fontFamily: _mono),
            decoration: InputDecoration(
              filled: true,
              fillColor: TranquilTheme.deepWater.withOpacity(0.3),
              contentPadding: const EdgeInsets.symmetric(vertical: 12),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(10),
                borderSide: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.3)),
              ),
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(10),
                borderSide: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.3)),
              ),
              focusedBorder: const OutlineInputBorder(
                borderRadius: BorderRadius.all(Radius.circular(10)),
                borderSide: BorderSide(color: TranquilTheme.glowCyan),
              ),
            ),
          ),
        ],
      );
}

class _ConsoleButton extends StatelessWidget {
  const _ConsoleButton({required this.enabled, required this.onTap});
  final bool enabled;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: enabled ? onTap : null,
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 14),
          decoration: BoxDecoration(
            color: _surfaceGlass,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: enabled ? TranquilTheme.glowCyan.withOpacity(0.4) : _borderGlow),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.terminal_rounded, size: 18, color: enabled ? TranquilTheme.glowCyan : _textDim),
              const SizedBox(width: 10),
              Text(
                'Live Console',
                style: TextStyle(
                  color: enabled ? Colors.white : _textDim,
                  fontSize: 13, 
                  fontWeight: FontWeight.w600,
                  fontFamily: _mono,
                ),
              ),
            ],
          ),
        ),
      );
}

class _FilesButton extends StatelessWidget {
  const _FilesButton({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 14),
          decoration: BoxDecoration(
            color: _surfaceGlass,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: _borderGlow),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.folder_open_rounded, size: 18, color: Colors.white),
              const SizedBox(width: 10),
              Text(
                'File Manager',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 13, 
                  fontWeight: FontWeight.w600,
                  fontFamily: _mono,
                ),
              ),
            ],
          ),
        ),
      );
}

class _BansButton extends StatelessWidget {
  const _BansButton({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 14),
          decoration: BoxDecoration(
            color: _surfaceGlass,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: _borderGlow),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.block_rounded, size: 18, color: Colors.white),
              const SizedBox(width: 10),
              Text(
                'Bans',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 13, 
                  fontWeight: FontWeight.w600,
                  fontFamily: _mono,
                ),
              ),
            ],
          ),
        ),
      );
}

class _ModsButton extends StatelessWidget {
  const _ModsButton({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 14),
          decoration: BoxDecoration(
            color: TranquilTheme.glowCyan.withOpacity(0.07),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: TranquilTheme.glowCyan.withOpacity(0.4)),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.extension_rounded, size: 18, color: TranquilTheme.glowCyan),
              const SizedBox(width: 10),
              Text(
                'Manage Mods',
                style: TextStyle(
                  color: TranquilTheme.glowCyan,
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  fontFamily: _mono,
                ),
              ),
            ],
          ),
    );
}

class _PropertiesButton extends StatelessWidget {
  const _PropertiesButton({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 14),
          decoration: BoxDecoration(
            color: TranquilTheme.glowCyan.withOpacity(0.07),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: TranquilTheme.glowCyan.withOpacity(0.4)),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.tune_rounded, size: 18, color: TranquilTheme.glowCyan),
              const SizedBox(width: 10),
              Text(
                'Properties',
                style: TextStyle(
                  color: TranquilTheme.glowCyan,
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  fontFamily: _mono,
                ),
              ),
            ],
          ),
        ),
      );
}

class _StatusBanner extends StatelessWidget {
  const _StatusBanner({required this.message, required this.ok});
  final String message;
  final bool ok;

  @override
  Widget build(BuildContext context) {
    final color = ok ? TranquilTheme.glowCyan : _errorRed;
    final icon = ok ? Icons.check_circle_outline_rounded : Icons.error_outline_rounded;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: color.withOpacity(0.1),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withOpacity(0.3)),
      ),
      child: Row(
        children: [
          Icon(icon, color: color, size: 18),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              message,
              style: TextStyle(color: color, fontSize: 12, fontFamily: _mono, height: 1.4),
            ),
          ),
        ],
      ),
    );
  }
}

class _InfoHint extends StatelessWidget {
  const _InfoHint({required this.icon, required this.text, required this.color});
  final IconData icon;
  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: color.withOpacity(0.08),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: color.withOpacity(0.2)),
        ),
        child: Row(
          children: [
            Icon(icon, color: color, size: 14),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                text,
                style: TextStyle(color: color, fontSize: 11, fontFamily: _mono),
              ),
            ),
          ],
        ),
      );
}

class _GlassSubLabel extends StatelessWidget {
  const _GlassSubLabel(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Text(
        text,
        style: TextStyle(color: TranquilTheme.textMuted, fontSize: 10, fontWeight: FontWeight.w600, letterSpacing: 1.2, fontFamily: _mono),
      );
}