import 'dart:ui';
import 'package:flutter/material.dart';

import '../../state/app_state.dart';
import '../theme/tranquil_theme.dart';
import '../widgets/glass_card.dart';

class ServerPropertiesScreen extends StatefulWidget {
  const ServerPropertiesScreen({
    super.key,
    required this.state,
    required this.serverId,
  });

  final AppState state;
  final String serverId;

  @override
  State<ServerPropertiesScreen> createState() => _ServerPropertiesScreenState();
}

class _ServerPropertiesScreenState extends State<ServerPropertiesScreen> {
  bool _isLoading = true;
  bool _isSaving = false;
  String? _error;

  Map<String, dynamic> _properties = {};
  Map<String, dynamic> _originalProperties = {};

  // Form controllers for String/Int values
  final Map<String, TextEditingController> _controllers = {};

  @override
  void initState() {
    super.initState();
    _loadProperties();
  }

  @override
  void dispose() {
    for (var c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _loadProperties() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });

    try {
      final props = await widget.state.api.getServerProperties(widget.serverId);
      setState(() {
        _properties = Map<String, dynamic>.from(props);
        _originalProperties = Map<String, dynamic>.from(props);
        _initControllers();
        _isLoading = false;
      });
    } catch (e) {
      setState(() {
        _error = e.toString();
        _isLoading = false;
      });
    }
  }

  void _initControllers() {
    _controllers.clear();
    for (var entry in _properties.entries) {
      if (entry.value is! bool) {
        _controllers[entry.key] = TextEditingController(text: entry.value.toString());
      }
    }
  }

  Future<void> _saveProperties() async {
    // Collect all updated values
    final updates = <String, dynamic>{};
    
    // Check strings/ints
    for (var key in _controllers.keys) {
      final original = _originalProperties[key];
      final current = _controllers[key]!.text;
      if (current.toString() != original.toString()) {
        if (original is int) {
          updates[key] = int.tryParse(current) ?? original;
        } else {
          updates[key] = current;
        }
      }
    }
    
    // Check bools
    for (var entry in _properties.entries) {
      if (entry.value is bool) {
        if (entry.value != _originalProperties[entry.key]) {
          updates[entry.key] = entry.value;
        }
      }
    }

    if (updates.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        backgroundColor: TranquilTheme.deepWater,
        content: const Text('No changes to save.',
            style: TextStyle(color: Colors.white, fontFamily: 'monospace', fontSize: 12)),
      ));
      return;
    }

    setState(() => _isSaving = true);
    try {
      await widget.state.api.updateServerProperties(widget.serverId, updates);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          backgroundColor: TranquilTheme.glowCyan,
          content: const Text('Properties saved. Restart server for changes to take effect.',
              style: TextStyle(color: Colors.black87, fontFamily: 'monospace', fontSize: 12)),
        ));
        Navigator.pop(context, true); // Signal success
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          backgroundColor: Colors.redAccent,
          content: Text('Error saving: $e',
              style: const TextStyle(color: Colors.white, fontFamily: 'monospace', fontSize: 12)),
        ));
        setState(() => _isSaving = false);
      }
    }
  }

  Widget _buildField(String key, String label, String? description, List<String>? options) {
    if (!_properties.containsKey(key)) return const SizedBox.shrink();

    final val = _properties[key];
    
    // BOOLS
    if (val is bool) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(label, style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600)),
                  if (description != null) ...[
                    const SizedBox(height: 2),
                    Text(description, style: const TextStyle(color: Colors.white38, fontSize: 11, fontFamily: 'monospace')),
                  ]
                ],
              ),
            ),
            Switch(
              value: val,
              onChanged: (v) => setState(() => _properties[key] = v),
              activeColor: TranquilTheme.glowCyan,
              activeTrackColor: TranquilTheme.glowCyan.withOpacity(.25),
              inactiveThumbColor: Colors.white38,
              inactiveTrackColor: Colors.white10,
            ),
          ],
        ),
      );
    }

    // STRINGS / INTS with pre-defined options (Dropdown)
    if (options != null) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600)),
            if (description != null) ...[
              const SizedBox(height: 2),
              Text(description, style: const TextStyle(color: Colors.white38, fontSize: 11, fontFamily: 'monospace')),
            ],
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              decoration: BoxDecoration(
                color: Colors.white.withOpacity(0.05),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: Colors.white12),
              ),
              child: DropdownButtonHideUnderline(
                child: DropdownButton<String>(
                  value: _controllers[key]!.text,
                  dropdownColor: TranquilTheme.deepWater,
                  isExpanded: true,
                  style: const TextStyle(color: Colors.white, fontSize: 13, fontFamily: 'monospace'),
                  icon: const Icon(Icons.arrow_drop_down_rounded, color: Colors.white38),
                  items: options.map((opt) {
                    return DropdownMenuItem(
                      value: opt,
                      child: Text(opt),
                    );
                  }).toList(),
                  onChanged: (v) {
                    if (v != null) setState(() => _controllers[key]!.text = v);
                  },
                ),
              ),
            ),
          ],
        ),
      );
    }

    // STRINGS / INTS (Text Field)
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600)),
          if (description != null) ...[
            const SizedBox(height: 2),
            Text(description, style: const TextStyle(color: Colors.white38, fontSize: 11, fontFamily: 'monospace')),
          ],
          const SizedBox(height: 8),
          TextField(
            controller: _controllers[key],
            style: const TextStyle(color: Colors.white, fontSize: 13, fontFamily: 'monospace'),
            decoration: InputDecoration(
              isDense: true,
              filled: true,
              fillColor: Colors.white.withOpacity(0.05),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: const BorderSide(color: Colors.white12),
              ),
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: const BorderSide(color: Colors.white12),
              ),
              focusedBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.5)),
              ),
            ),
            keyboardType: (val is int) ? TextInputType.number : TextInputType.text,
          ),
        ],
      ),
    );
  }

  Widget _buildCategory(String title, List<Widget> children) {
    // Only show category if at least one child is actually rendered (not a SizedBox.shrink)
    final visibleChildren = children.where((w) => w is! SizedBox).toList();
    if (visibleChildren.isEmpty) return const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: 16),
        Text(
          title.toUpperCase(),
          style: TextStyle(
              color: TranquilTheme.textMuted,
              fontSize: 10,
              fontFamily: 'monospace',
              letterSpacing: 1.5),
        ),
        const SizedBox(height: 8),
        GlassCard(
          child: Column(
            children: [
              for (int i = 0; i < visibleChildren.length; i++) ...[
                visibleChildren[i],
                if (i < visibleChildren.length - 1)
                  Divider(height: 16, color: TranquilTheme.glowCyan.withOpacity(0.12)),
              ],
            ],
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: SafeArea(
        child: Column(
          children: [
            // Header
            ClipRect(
              child: BackdropFilter(
                filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
                child: Container(
                  padding: const EdgeInsets.fromLTRB(16, 16, 20, 14),
                  decoration: BoxDecoration(
                    color: TranquilTheme.deepWater.withOpacity(0.4),
                    border: Border(
                      bottom: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.2)),
                    ),
                  ),
                  child: Row(
                    children: [
                      IconButton(
                        icon: const Icon(Icons.arrow_back_ios_new_rounded, color: Colors.white70, size: 20),
                        onPressed: () => Navigator.pop(context),
                      ),
                      const SizedBox(width: 8),
                      const Icon(Icons.tune_rounded, color: TranquilTheme.glowCyan, size: 20),
                      const SizedBox(width: 10),
                      const Text('Properties',
                          style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.bold)),
                    ],
                  ),
                ),
              ),
            ),

            Expanded(
              child: _isLoading
                  ? const Center(
                      child: CircularProgressIndicator(color: TranquilTheme.glowCyan))
                  : _error != null
                      ? Center(
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              const Icon(Icons.error_outline_rounded, color: Colors.redAccent, size: 48),
                              const SizedBox(height: 16),
                              Text(_error!, style: const TextStyle(color: Colors.white70)),
                              const SizedBox(height: 16),
                              TextButton.icon(
                                onPressed: _loadProperties,
                                icon: const Icon(Icons.refresh_rounded, color: TranquilTheme.glowCyan),
                                label: const Text('Retry', style: TextStyle(color: TranquilTheme.glowCyan)),
                              )
                            ],
                          ),
                        )
                      : SingleChildScrollView(
                          padding: const EdgeInsets.fromLTRB(16, 8, 16, 100),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              _buildCategory('World', [
                                _buildField('level-name', 'Level Name', 'The name of your world.', null),
                                _buildField('level-seed', 'Level Seed', 'The seed used for world generation.', null),
                                _buildField('max-players', 'Max Players', 'Maximum concurrent players.', null),
                                _buildField('view-distance', 'View Distance', 'Server-side render distance.', null),
                              ]),
                              _buildCategory('Gameplay', [
                                _buildField('gamemode', 'Game Mode', 'The default mode for new players.', ['survival', 'creative', 'adventure', 'spectator']),
                                _buildField('difficulty', 'Difficulty', 'The difficulty level of the server.', ['peaceful', 'easy', 'normal', 'hard']),
                                _buildField('allow-cheats', 'Allow Cheats', 'Enable or disable cheats (Bedrock).', null),
                                _buildField('allow-flight', 'Allow Flight', 'Permit players to fly in survival mode (Java).', null),
                                _buildField('enable-command-block', 'Command Blocks', 'Enable command blocks (Java).', null),
                              ]),
                              _buildCategory('Network & Server', [
                                _buildField('server-name', 'Server Name', 'Name displayed in the server list (Bedrock).', null),
                                _buildField('motd', 'Message of the Day', 'Message displayed in the server list (Java).', null),
                                _buildField('online-mode', 'Online Mode', 'Verify players with Xbox Live / Mojang.', null),
                              ]),
                            ],
                          ),
                        ),
            ),
          ],
        ),
      ),
      floatingActionButton: (!_isLoading && _error == null)
          ? FloatingActionButton.extended(
              onPressed: _isSaving ? null : _saveProperties,
              backgroundColor: TranquilTheme.glowCyan,
              icon: _isSaving
                  ? const SizedBox(
                      width: 20, height: 20,
                      child: CircularProgressIndicator(color: Colors.black54, strokeWidth: 2))
                  : const Icon(Icons.save_rounded, color: Colors.black87),
              label: Text(
                _isSaving ? 'SAVING...' : 'SAVE CHANGES',
                style: const TextStyle(color: Colors.black87, fontWeight: FontWeight.bold, fontFamily: 'monospace'),
              ),
            )
          : null,
    );
  }
}
