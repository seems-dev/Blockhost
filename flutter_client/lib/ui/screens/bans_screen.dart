import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../api/blockhost_api.dart';
import '../../models/ban_models.dart';
import '../../state/app_state.dart';
import '../theme/tranquil_theme.dart';
import '../widgets/glass_card.dart';

const _mono = 'monospace';
const _surfaceGlass = Color(0x80051923);
const _borderGlow = Color(0x4D64FFDA);
const _textDim = Color(0xFFB0BEC5);
const _errorRed = Color(0xFFFF6B6B);
const _successGreen = Color(0xFF5DADE2);

class BansScreen extends StatefulWidget {
  const BansScreen({
    super.key,
    required this.state,
    required this.serverId,
    required this.worldName,
  });

  final AppState state;
  final String serverId;
  final String worldName;

  @override
  State<BansScreen> createState() => _BansScreenState();
}

class _BansScreenState extends State<BansScreen> {
  bool _busy = false;
  String? _status;
  bool _statusOk = true;
  List<Ban> _activeBans = [];
  List<Ban> _banHistory = [];
  bool _showHistory = false;
  List<PlayerInfo> _onlinePlayers = [];
  String? _selectedPlayerName;

  final _xuIdCtrl = TextEditingController();
  final _playerNameCtrl = TextEditingController();
  final _reasonCtrl = TextEditingController();
  final _durationCtrl = TextEditingController(text: '0');

  @override
  void initState() {
    super.initState();
    _loadBans();
    _loadOnlinePlayers();
  }

  @override
  void dispose() {
    _xuIdCtrl.dispose();
    _playerNameCtrl.dispose();
    _reasonCtrl.dispose();
    _durationCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadBans() async {
    if (!mounted) return;
    setState(() {
      _busy = true;
      _status = null;
    });
    try {
      final activeBans = await widget.state.api.listBans(widget.serverId);
      final history = await widget.state.api.listBanHistory(widget.serverId);
      if (mounted) {
        setState(() {
          _activeBans = activeBans.items;
          _banHistory = history.items;
          _busy = false;
          _status = 'Bans loaded';
          _statusOk = true;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _status = 'Error loading bans: $e';
          _statusOk = false;
        });
      }
    }
  }

  Future<void> _loadOnlinePlayers() async {
    try {
      final players = await widget.state.api.getOnlinePlayers(widget.serverId);
      if (mounted) {
        setState(() {
          _onlinePlayers = players;
        });
      }
    } catch (e) {
      // Silently fail - online players are optional
    }
  }

  void _selectPlayer(String? playerName) {
    if (playerName == null || playerName.isEmpty) {
      setState(() {
        _selectedPlayerName = null;
      });
      return;
    }
    
    // Find the selected player in the list
    final player = _onlinePlayers.firstWhere(
      (p) => p.name == playerName,
      orElse: () => PlayerInfo(name: playerName),
    );
    
    setState(() {
      _selectedPlayerName = playerName;
      _playerNameCtrl.text = player.name;
      // Auto-fill XUID if available
      if (player.xuid != null && player.xuid!.isNotEmpty) {
        _xuIdCtrl.text = player.xuid!;
      }
    });
  }

  Future<void> _createBan() async {
    final xuid = _xuIdCtrl.text.trim();
    final playerName = _playerNameCtrl.text.trim();
    final reason = _reasonCtrl.text.trim();
    final durationStr = _durationCtrl.text.trim();

    if (xuid.isEmpty || playerName.isEmpty) {
      setState(() {
        _status = 'XUID and player name are required';
        _statusOk = false;
      });
      return;
    }

    int? durationSeconds;
    if (durationStr.isNotEmpty && durationStr != '0') {
      durationSeconds = int.tryParse(durationStr);
      if (durationSeconds == null || durationSeconds <= 0) {
        setState(() {
          _status = 'Duration must be a positive number (seconds)';
          _statusOk = false;
        });
        return;
      }
    }

    if (!mounted) return;
    setState(() {
      _busy = true;
      _status = null;
    });

    try {
      await widget.state.api.createBan(
        widget.serverId,
        xuid: xuid,
        playerName: playerName,
        reason: reason.isEmpty ? null : reason,
        durationSeconds: durationSeconds,
      );

      if (mounted) {
        setState(() {
          _xuIdCtrl.clear();
          _playerNameCtrl.clear();
          _reasonCtrl.clear();
          _durationCtrl.text = '0';
          _status = 'Ban created successfully';
          _statusOk = true;
        });
        _loadBans();
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _status = 'Error creating ban: $e';
          _statusOk = false;
        });
      }
    }
  }

  Future<void> _unbanPlayer(Ban ban) async {
    if (!mounted) return;
    setState(() {
      _busy = true;
      _status = null;
    });

    try {
      await widget.state.api.deleteBan(widget.serverId, ban.ban_id);
      if (mounted) {
        setState(() {
          _status = 'Ban removed successfully';
          _statusOk = true;
        });
        _loadBans();
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _status = 'Error removing ban: $e';
          _statusOk = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF051923),
      body: Column(
        children: [
          // Header
          Padding(
            padding: const EdgeInsets.all(16),
            child: Row(
              children: [
                IconButton(
                  icon: const Icon(Icons.arrow_back, color: Colors.white70),
                  onPressed: () => Navigator.pop(context),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'Ban Management',
                        style: TextStyle(
                          fontSize: 24,
                          fontWeight: FontWeight.bold,
                          color: Colors.white,
                        ),
                      ),
                      Text(
                        widget.worldName,
                        style: const TextStyle(
                          fontSize: 14,
                          color: _textDim,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                // Status message
                if (_status != null)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 16),
                    child: Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: _statusOk
                            ? Color.fromARGB(128, 93, 173, 226)
                            : Color.fromARGB(128, 255, 107, 107),
                        border: Border.all(
                          color: _statusOk ? _successGreen : _errorRed,
                          width: 1.5,
                        ),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text(
                        _status!,
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 13,
                        ),
                      ),
                    ),
                  ),

                // Create ban form
                GlassCard(
                  child: Padding(
                    padding: const EdgeInsets.all(20),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          'Create Ban',
                          style: TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.bold,
                            color: Colors.white,
                          ),
                        ),
                        const SizedBox(height: 16),
                        // Player selection dropdown
                        if (_onlinePlayers.isNotEmpty)
                          Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Text(
                                'Select Online Player (optional)',
                                style: TextStyle(
                                  fontSize: 12,
                                  color: _textDim,
                                  fontWeight: FontWeight.w500,
                                ),
                              ),
                              const SizedBox(height: 8),
                              Container(
                                decoration: BoxDecoration(
                                  color: _surfaceGlass,
                                  border: Border.all(color: _borderGlow, width: 1),
                                  borderRadius: BorderRadius.circular(8),
                                ),
                                child: DropdownButton<String>(
                                  value: _selectedPlayerName,
                                  isExpanded: true,
                                  underline: SizedBox(),
                                  dropdownColor: const Color(0xFF051923),
                                  items: [
                                    DropdownMenuItem(
                                      value: null,
                                      child: Padding(
                                        padding: const EdgeInsets.symmetric(horizontal: 16),
                                        child: Text(
                                          'Choose a player...',
                                          style: TextStyle(
                                            color: _textDim,
                                            fontSize: 14,
                                          ),
                                        ),
                                      ),
                                    ),
                                    ..._onlinePlayers.map(
                                      (player) => DropdownMenuItem(
                                        value: player.name,
                                        child: Padding(
                                          padding: const EdgeInsets.symmetric(horizontal: 16),
                                          child: Text(
                                            '${player.name}${player.xuid != null ? ' (${player.xuid})' : ''}',
                                            style: const TextStyle(
                                              color: Colors.white,
                                              fontSize: 14,
                                            ),
                                          ),
                                        ),
                                      ),
                                    ),
                                  ],
                                  onChanged: _selectPlayer,
                                ),
                              ),
                              const SizedBox(height: 16),
                            ],
                          ),
                        _buildTextField(
                          controller: _xuIdCtrl,
                          label: 'Player XUID',
                          hint: 'e.g., 2535450987654321',
                          enabled: !_busy,
                        ),
                        const SizedBox(height: 12),
                        _buildTextField(
                          controller: _playerNameCtrl,
                          label: 'Player Name',
                          hint: 'e.g., Steve',
                          enabled: !_busy,
                        ),
                        const SizedBox(height: 12),
                        _buildTextField(
                          controller: _reasonCtrl,
                          label: 'Reason (optional)',
                          hint: 'e.g., Griefing',
                          enabled: !_busy,
                        ),
                        const SizedBox(height: 12),
                        _buildTextField(
                          controller: _durationCtrl,
                          label: 'Duration (seconds, 0 = permanent)',
                          hint: '0',
                          enabled: !_busy,
                          keyboardType: TextInputType.number,
                        ),
                        const SizedBox(height: 16),
                        SizedBox(
                          width: double.infinity,
                          child: ElevatedButton(
                            onPressed: _busy ? null : _createBan,
                            style: ElevatedButton.styleFrom(
                              backgroundColor: _errorRed,
                              disabledBackgroundColor:
                                  _errorRed.withOpacity(0.5),
                              padding: const EdgeInsets.symmetric(vertical: 12),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(8),
                              ),
                            ),
                            child: Text(
                              _busy ? 'Creating...' : 'Create Ban',
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 16,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),

                const SizedBox(height: 24),

                // Tab to switch between active bans and history
                Row(
                  children: [
                    Expanded(
                      child: GestureDetector(
                        onTap: () =>
                            setState(() => _showHistory = false),
                        child: Container(
                          padding: const EdgeInsets.symmetric(vertical: 12),
                          decoration: BoxDecoration(
                            border: Border(
                              bottom: BorderSide(
                                color: !_showHistory ? _successGreen : Colors.transparent,
                                width: 2,
                              ),
                            ),
                          ),
                          child: Text(
                            'Active Bans (${_activeBans.length})',
                            textAlign: TextAlign.center,
                            style: TextStyle(
                              color: !_showHistory ? Colors.white : _textDim,
                              fontSize: 14,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                      ),
                    ),
                    Expanded(
                      child: GestureDetector(
                        onTap: () => setState(() => _showHistory = true),
                        child: Container(
                          padding: const EdgeInsets.symmetric(vertical: 12),
                          decoration: BoxDecoration(
                            border: Border(
                              bottom: BorderSide(
                                color: _showHistory ? _successGreen : Colors.transparent,
                                width: 2,
                              ),
                            ),
                          ),
                          child: Text(
                            'History (${_banHistory.length})',
                            textAlign: TextAlign.center,
                            style: TextStyle(
                              color: _showHistory ? Colors.white : _textDim,
                              fontSize: 14,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                      ),
                    ),
                  ],
                ),

                const SizedBox(height: 16),

                // Bans list
                ...((_showHistory ? _banHistory : _activeBans).isEmpty
                    ? [
                        Center(
                          child: Text(
                            _showHistory
                                ? 'No ban history'
                                : 'No active bans',
                            style: const TextStyle(
                              color: _textDim,
                              fontSize: 14,
                            ),
                          ),
                        ),
                      ]
                    : (_showHistory ? _banHistory : _activeBans)
                        .map((ban) => _buildBanCard(ban))
                        .toList()),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildBanCard(Ban ban) {
    return GlassCard(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        ban.player_name,
                        style: const TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                          color: Colors.white,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'XUID: ${ban.xuid}',
                        style: const TextStyle(
                          fontSize: 12,
                          color: _textDim,
                          fontFamily: _mono,
                        ),
                      ),
                    ],
                  ),
                ),
                if (ban.active)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: _errorRed.withOpacity(0.2),
                      border: Border.all(color: _errorRed, width: 1),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: const Text(
                      'ACTIVE',
                      style: TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                        color: _errorRed,
                      ),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 12),
            if (ban.reason != null && ban.reason!.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Reason',
                      style: TextStyle(
                        fontSize: 11,
                        color: _textDim,
                      ),
                    ),
                    Text(
                      ban.reason!,
                      style: const TextStyle(
                        fontSize: 13,
                        color: Colors.white70,
                      ),
                    ),
                  ],
                ),
              ),
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'Duration',
                        style: TextStyle(
                          fontSize: 11,
                          color: _textDim,
                        ),
                      ),
                      Text(
                        ban.durationDisplay,
                        style: const TextStyle(
                          fontSize: 13,
                          color: Colors.white70,
                        ),
                      ),
                    ],
                  ),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'Banned At',
                        style: TextStyle(
                          fontSize: 11,
                          color: _textDim,
                        ),
                      ),
                      Text(
                        DateFormat('MM/dd HH:mm').format(ban.banned_at),
                        style: const TextStyle(
                          fontSize: 13,
                          color: Colors.white70,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            if (ban.active)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: SizedBox(
                  width: double.infinity,
                  child: ElevatedButton.icon(
                    onPressed: _busy ? null : () => _unbanPlayer(ban),
                    icon: const Icon(Icons.lock_open, size: 16),
                    label: const Text('Unban'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: _successGreen,
                      disabledBackgroundColor: _successGreen.withOpacity(0.5),
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(6),
                      ),
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildTextField({
    required TextEditingController controller,
    required String label,
    required String hint,
    bool enabled = true,
    TextInputType keyboardType = TextInputType.text,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(
            fontSize: 12,
            color: _textDim,
            fontWeight: FontWeight.bold,
          ),
        ),
        const SizedBox(height: 6),
        TextField(
          controller: controller,
          enabled: enabled,
          keyboardType: keyboardType,
          style: const TextStyle(color: Colors.white, fontSize: 14),
          decoration: InputDecoration(
            hintText: hint,
            hintStyle: const TextStyle(color: Color(0xFF5A7A8A)),
            filled: true,
            fillColor: const Color(0x20051923),
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(6),
              borderSide: const BorderSide(color: Color(0x4D64FFDA)),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(6),
              borderSide: const BorderSide(color: Color(0x4D64FFDA)),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(6),
              borderSide: const BorderSide(color: Color(0xFF64FFDA), width: 2),
            ),
            disabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(6),
              borderSide: const BorderSide(
                color: Color(0x2064FFDA),
              ),
            ),
            contentPadding: const EdgeInsets.symmetric(
              horizontal: 12,
              vertical: 10,
            ),
          ),
        ),
      ],
    );
  }
}
