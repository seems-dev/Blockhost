import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../../state/app_state.dart';

const Color _darkBg = Color(0xFF13131A);
const Color _cardBg = Color(0xFF1C1C24);
const Color _accent = Color(0xFF06B6D4);
const Color _textGreen = Color(0xFF00E676);

class ServerConsoleScreen extends StatefulWidget {
  const ServerConsoleScreen({
    super.key,
    required this.state,
    required this.serverId,
    required this.worldName,
  });

  final AppState state;
  final String serverId;
  final String worldName;

  @override
  State<ServerConsoleScreen> createState() => _ServerConsoleScreenState();
}

class _ServerConsoleScreenState extends State<ServerConsoleScreen> {
  final _inputCtrl = TextEditingController();
  final ScrollController _scrollCtrl = ScrollController();
  
  WebSocketChannel? _channel;
  final List<Map<String, dynamic>> _logs = [];
  bool _connected = false;
  Timer? _reconnectTimer;
  
  @override
  void initState() {
    super.initState();
    _connect();
  }

  void _connect() {
    if (!mounted) return;
    try {
      final uri = widget.state.api.getConsoleWebSocketUri(widget.serverId);
      _channel = WebSocketChannel.connect(uri);
      setState(() => _connected = true);
      
      _channel!.stream.listen(
        (data) {
          try {
            final msg = jsonDecode(data as String);
            if (msg['type'] == 'history') {
              setState(() {
                _logs.clear();
                for (var log in msg['logs']) _logs.add(log);
              });
              _scrollToBottom();
            } else if (msg['type'] == 'log') {
              setState(() {
                _logs.add(msg['log']);
                if (_logs.length > 4000) _logs.removeAt(0);
              });
              _scrollToBottom();
            } else if (msg['type'] == 'error') {
              _appendSystemMessage('Error: ${msg['error']}');
            }
          } catch (e) {
            _appendSystemMessage('Failed to parse: $data');
          }
        },
        onDone: () {
          if (mounted) {
            setState(() => _connected = false);
            _appendSystemMessage('Connection closed. Reconnecting...');
            _scheduleReconnect();
          }
        },
        onError: (err) {
          if (mounted) {
            setState(() => _connected = false);
            _appendSystemMessage('Connection error: $err');
            _scheduleReconnect();
          }
        },
      );
    } catch (e) {
      _appendSystemMessage('Failed to connect: $e');
      _scheduleReconnect();
    }
  }

  void _scheduleReconnect() {
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 3), _connect);
  }
  
  void _appendSystemMessage(String msg) {
    setState(() {
      _logs.add({
        'ts': DateTime.now().toIso8601String(),
        'line': '[SYSTEM] $msg',
        'isSystem': true,
      });
    });
    _scrollToBottom();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollCtrl.hasClients) {
        _scrollCtrl.animateTo(
          _scrollCtrl.position.maxScrollExtent,
          duration: const Duration(milliseconds: 100),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  void dispose() {
    _reconnectTimer?.cancel();
    _channel?.sink.close();
    _inputCtrl.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  void _sendCommand() {
    final cmd = _inputCtrl.text.trim();
    if (cmd.isEmpty || !_connected || _channel == null) return;
    
    _channel!.sink.add(jsonEncode({
      'type': 'command',
      'command': cmd,
    }));
    _inputCtrl.clear();
  }

  String _formatTs(String? ts) {
    if (ts == null) return '';
    try {
      final dt = DateTime.parse(ts).toLocal();
      return '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}:${dt.second.toString().padLeft(2, '0')}';
    } catch (_) {
      return '';
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _darkBg,
      body: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Top App Bar
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
              child: Row(
                children: [
                  const Icon(Icons.grid_view_rounded, color: _accent, size: 20),
                  const SizedBox(width: 12),
                  const Text('EREX', style: TextStyle(color: _accent, fontSize: 14, fontWeight: FontWeight.bold, letterSpacing: 1.5)),
                  const Spacer(),
                  Container(
                    width: 32,
                    height: 32,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      border: Border.all(color: _accent.withOpacity(0.5)),
                      image: const DecorationImage(
                        image: AssetImage('assets/ic_launcher.png'),
                        fit: BoxFit.cover,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            
            const SizedBox(height: 16),
            
            // Header
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Server Console', style: TextStyle(color: Colors.white, fontSize: 26, fontWeight: FontWeight.bold)),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      const Text('Live output from instance ', style: TextStyle(color: Colors.white70, fontSize: 14)),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                        decoration: BoxDecoration(color: _accent.withOpacity(0.1), borderRadius: BorderRadius.circular(4)),
                        child: Text(widget.worldName.toUpperCase(), style: const TextStyle(color: _accent, fontSize: 12, fontWeight: FontWeight.bold)),
                      )
                    ],
                  ),
                  const SizedBox(height: 16),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                    decoration: BoxDecoration(
                      color: _accent.withOpacity(0.1),
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(color: _accent.withOpacity(0.3)),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Container(width: 6, height: 6, decoration: BoxDecoration(color: _connected ? _accent : Colors.orange, shape: BoxShape.circle)),
                        const SizedBox(width: 8),
                        Text(_connected ? 'ONLINE - LIVE STREAM' : 'RECONNECTING...', style: const TextStyle(color: _accent, fontSize: 11, fontWeight: FontWeight.bold, letterSpacing: 0.5)),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            
            const SizedBox(height: 24),
            
            // Console Box
            Expanded(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 20),
                child: Container(
                  decoration: BoxDecoration(
                    color: _cardBg,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: Colors.white.withOpacity(0.05)),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      // Mac-style Header
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        decoration: BoxDecoration(
                          border: Border(bottom: BorderSide(color: Colors.white.withOpacity(0.05))),
                        ),
                        child: Row(
                          children: [
                            const Icon(Icons.description_outlined, color: Colors.white54, size: 14),
                            const SizedBox(width: 8),
                            const Text('LATEST.LOG', style: TextStyle(color: Colors.white54, fontSize: 12, fontWeight: FontWeight.bold, letterSpacing: 1)),
                            const Spacer(),
                            Row(
                              children: [
                                _WinDot(), const SizedBox(width: 6),
                                _WinDot(), const SizedBox(width: 6),
                                _WinDot(),
                              ],
                            )
                          ],
                        ),
                      ),
                      
                      // Terminal
                      Expanded(
                        child: Container(
                          color: const Color(0xFF09090D),
                          child: ListView.builder(
                            controller: _scrollCtrl,
                            padding: const EdgeInsets.all(16),
                            itemCount: _logs.length,
                            itemBuilder: (context, i) {
                              final log = _logs[i];
                              final ts = _formatTs(log['ts']);
                              final line = log['line'] ?? '';
                              final isSystem = log['isSystem'] == true;
                              
                              return Padding(
                                padding: const EdgeInsets.symmetric(vertical: 4),
                                child: Text(
                                  ts.isEmpty ? line : '[$ts] $line',
                                  style: TextStyle(
                                    color: isSystem ? Colors.orangeAccent : _textGreen,
                                    fontFamily: 'monospace',
                                    fontSize: 13,
                                    height: 1.4,
                                  ),
                                ),
                              );
                            },
                          ),
                        ),
                      ),
                      
                      // Input
                      Container(
                        padding: const EdgeInsets.all(12),
                        color: _cardBg,
                        child: Row(
                          children: [
                            Expanded(
                              child: TextField(
                                controller: _inputCtrl,
                                style: const TextStyle(color: Colors.white, fontFamily: 'monospace'),
                                decoration: InputDecoration(
                                  hintText: 'Enter command...',
                                  hintStyle: const TextStyle(color: Colors.white54),
                                  filled: true,
                                  fillColor: Colors.black.withOpacity(0.3),
                                  contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide.none),
                                ),
                                onSubmitted: (_) => _sendCommand(),
                              ),
                            ),
                            const SizedBox(width: 8),
                            Container(
                              decoration: BoxDecoration(
                                color: _connected ? _accent : Colors.white10,
                                borderRadius: BorderRadius.circular(8),
                              ),
                              child: IconButton(
                                onPressed: _connected ? _sendCommand : null,
                                icon: const Icon(Icons.send_rounded),
                                color: _connected ? const Color(0xFF000000) : Colors.white54,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
            
            const SizedBox(height: 16),
            
            // Console Specific Bottom Nav
            Container(
              margin: const EdgeInsets.fromLTRB(20, 0, 20, 16),
              decoration: BoxDecoration(
                color: _cardBg,
                borderRadius: BorderRadius.circular(24),
                border: Border.all(color: Colors.white.withOpacity(0.05)),
              ),
              height: 64,
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: [
                  _ConsoleNavItem(icon: Icons.dashboard_rounded, label: 'DASHBOARD', onTap: () => Navigator.of(context).pop()),
                  const _ConsoleNavItem(icon: Icons.terminal_rounded, label: 'CONSOLE', selected: true),
                  _ConsoleNavItem(icon: Icons.folder_outlined, label: 'MARKETPLACE', onTap: () {}),
                  _ConsoleNavItem(icon: Icons.settings_outlined, label: 'SETTINGS', onTap: () {}),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _WinDot extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      width: 10,
      height: 10,
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.1),
        shape: BoxShape.circle,
      ),
    );
  }
}

class _ConsoleNavItem extends StatelessWidget {
  const _ConsoleNavItem({
    required this.icon,
    required this.label,
    this.selected = false,
    this.onTap,
  });

  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            padding: const EdgeInsets.all(6),
            decoration: selected
                ? BoxDecoration(
                    color: _accent.withOpacity(0.15),
                    shape: BoxShape.circle,
                    boxShadow: [
                      BoxShadow(color: _accent.withOpacity(0.3), blurRadius: 12, spreadRadius: 2)
                    ],
                  )
                : null,
            child: Icon(icon, color: selected ? _accent : Colors.white54, size: 20),
          ),
          const SizedBox(height: 2),
          Text(
            label,
            style: TextStyle(
              color: selected ? _accent : Colors.white54,
              fontSize: 9,
              fontWeight: FontWeight.bold,
              letterSpacing: 0.5,
            ),
          ),
        ],
      ),
    );
  }
}
