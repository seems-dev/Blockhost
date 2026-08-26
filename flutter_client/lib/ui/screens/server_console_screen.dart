import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../../state/app_state.dart';
import 'mod_screen.dart';
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
                for (var log in msg['logs']) {
                  _logs.add(log);
                }
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
            _appendSystemMessage('Failed to parse message: $data');
          }
        },
        onDone: () {
          if (mounted) {
            setState(() => _connected = false);
            _appendSystemMessage('Connection closed. Reconnecting in 3s...');
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
      backgroundColor: const Color(0xFF0D0014),
      appBar: AppBar(
        backgroundColor: const Color(0xFF120020),
        foregroundColor: Colors.white,
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Live Console', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
            Text('${widget.worldName} ${_connected ? '(Connected)' : '(Reconnecting...)'}', 
                style: TextStyle(fontSize: 12, color: _connected ? Colors.greenAccent : Colors.orangeAccent)),
          ],
        ),
      ),
      body: Column(
        children: [
          Expanded(
            child: Container(
              color: Colors.black,
              child: ListView.builder(
                controller: _scrollCtrl,
                padding: const EdgeInsets.all(8),
                itemCount: _logs.length,
                itemBuilder: (context, i) {
                  final log = _logs[i];
                  final ts = _formatTs(log['ts']);
                  final line = log['line'] ?? '';
                  final isSystem = log['isSystem'] == true;
                  
                  return Padding(
                    padding: const EdgeInsets.symmetric(vertical: 2),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        if (ts.isNotEmpty)
                          Text('[$ts] ', style: const TextStyle(color: Colors.white54, fontFamily: 'monospace', fontSize: 12)),
                        Expanded(
                          child: Text(
                            line,
                            style: TextStyle(
                              color: isSystem ? Colors.orangeAccent : Colors.white70,
                              fontFamily: 'monospace',
                              fontSize: 13,
                            ),
                          ),
                        ),
                      ],
                    ),
                  );
                },
              ),
            ),
          ),
          Container(
            padding: const EdgeInsets.all(8),
            color: const Color(0xFF120020),
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
                      fillColor: Colors.white.withOpacity(.05),
                      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide.none),
                    ),
                    onSubmitted: (_) => _sendCommand(),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton(
                  onPressed: _connected ? _sendCommand : null,
                  icon: const Icon(Icons.send_rounded),
                  color: const Color(0xFFCC44FF),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
