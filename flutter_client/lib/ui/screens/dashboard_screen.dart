import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../api/erex_api.dart';
import '../../api/cached_api.dart';
import '../../services/minecraft_launcher.dart';
import '../../state/app_state.dart';
import 'backup_screen.dart';
import 'server_control_screen.dart';
import 'server_console_screen.dart';
import 'create_server_screen.dart';
import 'plans_screen.dart';
import 'software_switch_screen.dart';

import '../theme/tranquil_theme.dart';
import '../widgets/glass_card.dart';
import '../widgets/glowing_button.dart';

// ─── Design tokens ────────────────────────────────────────────────────────────
const _bg = Colors.transparent;
const _surface = Color(0x80051923); // deepWater 50%
const _surfaceAlt = Color(0x60051923); // deepWater 38%
const _border = Color(0x4D64FFDA); // glowCyan 30%
const _borderBright = Color(0x9964FFDA); // glowCyan 60%
const _green = Color(0xFF64FFDA); // glowCyan
const _greenDim = Color(0xFF0083B0); // buttonGradient end
const _text = Colors.white;
const _muted = Color(0xFFA1A1AA); // zinc 400
const _mutedBright = Color(0xFFE4E4E7); // zinc 200
const _mono = 'monospace';

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key, required this.state});
  final AppState state;

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen>
    with WidgetsBindingObserver {
  bool busy = false;
  String status = '';
  List<dynamic> servers = [];
  String? selectedServerId;

  Map<String, dynamic>? detail;
  Map<String, dynamic>? stats;
  Map<String, dynamic>? config;
  Map<String, dynamic>? subscription;
  final Map<String, Map<String, dynamic>> _listStats = {};
  Timer? _liveTimer;
  bool _isScreenVisible = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _refresh();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _liveTimer?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _isScreenVisible = true;
      if (selectedServerId != null) _startLiveUpdates();
    } else {
      _isScreenVisible = false;
      _stopLiveUpdates();
    }
  }

  int _getPollingInterval() {
    final state = detail?['state'] as String? ?? '';
    if (state == 'starting' || state == 'restarting') return 2;
    if (state == 'running') return 10;
    return 30; // stopped
  }

  void _startLiveUpdates({bool immediate = true}) {
    _liveTimer?.cancel();
    if (!_isScreenVisible || selectedServerId == null) return;

    if (immediate) _pollLive();

    _liveTimer = Timer(Duration(seconds: _getPollingInterval()), () {
      if (mounted && _isScreenVisible && selectedServerId != null) {
        _startLiveUpdates(immediate: true);
      }
    });
  }

  void _stopLiveUpdates() {
    _liveTimer?.cancel();
    _liveTimer = null;
  }

  Future<void> _pollLive() async {
    final id = selectedServerId;
    if (id == null || !mounted || !_isScreenVisible) return;
    try {
      // Always fetch a fresh snapshot for the selected server so live
      // CPU/RAM/player data is never served from the 5-second cache.
      final api = widget.state.api;
      final snapshot = api is CachedErexApi
          ? await api.refreshServer(id)
          : await api.getServer(id);
      if (!mounted || selectedServerId != id) return;
      setState(() => _applyServerSnapshot(snapshot));
    } catch (_) {}
  }

  Future<void> _refresh({bool forceNetwork = false}) async {
    setState(() {
      busy = true;
      status = '';
    });
    try {
      // Pull-to-refresh bypasses the cache so the user always gets the
      // latest data.  initState and navigation returns use the cache.
      final api = widget.state.api;
      servers = forceNetwork && api is CachedErexApi
          ? await api.refreshServers()
          : await api.listServers();
      _listStats.clear();
      
      // Fetch stats for running servers to populate the list cards
      if (mounted) {
        for (final server in servers) {
          if (server is Map<String, dynamic> && server['state'] == 'running') {
            final id = server['id']?.toString();
            if (id != null) {
              api.getServerStats(id).then((statsData) {
                if (mounted) {
                  setState(() {
                    _listStats[id] = statsData;
                  });
                }
              }).catchError((_) {});
            }
          }
        }
      }
    } on ApiException catch (e) {
      setState(() => status = e.message);
    } catch (e) {
      setState(() => status = e.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  void _applyServerSnapshot(Map<String, dynamic> snapshot) {
    final rawServer = snapshot['server'];
    final rawStats = snapshot['stats'];
    final rawConfig = snapshot['config'];
    final nextDetail = rawServer is Map
        ? Map<String, dynamic>.from(rawServer)
        : Map<String, dynamic>.from(snapshot);
    if (rawConfig is Map) {
      nextDetail['mc_config'] = Map<String, dynamic>.from(rawConfig);
      config = {
        'id': nextDetail['id'],
        'mc_config': Map<String, dynamic>.from(rawConfig),
      };
    } else {
      config = {
        'id': nextDetail['id'],
        'mc_config': nextDetail['mc_config'] ?? {},
      };
    }
    detail = nextDetail;
    stats = rawStats is Map ? Map<String, dynamic>.from(rawStats) : null;
  }

  Future<void> _launchMinecraft(Map<String, dynamic> server) async {
    final launched = await MinecraftLauncher.launchServer(
      serverName: server['world_name']?.toString() ?? 'Erex Server',
      address: server['shareable_address']?.toString() ?? '',
    );
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      backgroundColor: _surface,
      content: Text(
        launched
            ? 'Opening Minecraft…'
            : 'Minecraft not installed or cannot open this server.',
        style: const TextStyle(color: _text, fontFamily: _mono, fontSize: 12),
      ),
    ));
  }

  Map<String, dynamic>? _findServer(String id) {
    for (final server in servers) {
      if (server is Map<String, dynamic> && server['id'] == id) return server;
    }
    return null;
  }

  Future<void> _toggle(String id,
      {bool launchWhenRunning = false,
      Map<String, dynamic>? launchServer}) async {
    setState(() => busy = true);
    try {
      final result = await widget.state.api.toggleServer(id);
      final newState = result['state']?.toString();
      if (mounted &&
          selectedServerId == id &&
          detail != null &&
          newState != null) {
        setState(() {
          detail = Map<String, dynamic>.from(detail!)..['state'] = newState;
        });
        _startLiveUpdates();
      }
      await _refresh();
      if (launchWhenRunning && newState == 'running') {
        await _launchMinecraft(
            _findServer(id) ?? launchServer ?? detail ?? {'id': id});
      }
    } on ApiException catch (e) {
      setState(() => status = e.message);
    } catch (e) {
      setState(() => status = e.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> _loadDetail(String id) async {
    _stopLiveUpdates();
    setState(() {
      busy = true;
      selectedServerId = id;
      detail = null;
      stats = null;
    });
    try {
      final snapshot = await widget.state.api.getServer(id);
      final subData = await widget.state.api.getServerSubscription(id);
      if (!mounted) return;
      setState(() {
        _applyServerSnapshot(snapshot);
        subscription = subData['subscription'] as Map<String, dynamic>?;
      });
      _startLiveUpdates(immediate: false);
    } on ApiException catch (e) {
      setState(() => status = e.message);
    } catch (e) {
      setState(() => status = e.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  void _closeDetail() {
    _stopLiveUpdates();
    setState(() {
      selectedServerId = null;
      detail = null;
      stats = null;
      config = null;
      subscription = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (selectedServerId != null && detail != null) {
      return _ServerDetailView(
        detail: detail!,
        stats: stats,
        subscription: subscription,
        busy: busy,
        onBack: _closeDetail,
        onToggle: () => _toggle(
          selectedServerId!,
          launchWhenRunning: (detail!['state'] as String? ?? '') != 'running',
          launchServer: detail,
        ),
        onLaunch: () => _launchMinecraft(detail!),
        onOpenConsole: () {
          Navigator.of(context).push(MaterialPageRoute<void>(
            builder: (_) => ServerConsoleScreen(
              state: widget.state,
              serverId: selectedServerId!,
              worldName: detail!['world_name']?.toString() ?? 'Server',
            ),
          ));
        },
        onOpenSettings: () async {
          final online = _extractOnlinePlayerNames(stats?['online_players_list']);
          _isScreenVisible = false;
          _stopLiveUpdates();
          await Navigator.of(context).push(MaterialPageRoute<void>(
            builder: (_) => ServerControlScreen(
              state: widget.state,
              serverId: selectedServerId!,
              worldName: detail!['world_name']?.toString() ?? 'Server',
              isRunning: (detail!['state'] as String? ?? '') == 'running',
              onlinePlayers: online,
            ),
          ));
          if (mounted) {
            _isScreenVisible = true;
            if (selectedServerId != null) _startLiveUpdates();
          }
        },
        onOpenBackups: () async {
          _isScreenVisible = false;
          _stopLiveUpdates();
          await Navigator.of(context).push(MaterialPageRoute<void>(
            builder: (_) => BackupScreen(
              state: widget.state,
              serverId: selectedServerId!,
              worldName: detail!['world_name']?.toString() ?? 'Server',
            ),
          ));
          if (mounted) {
            _isScreenVisible = true;
            if (selectedServerId != null) _startLiveUpdates();
          }
        },
        onSwitchSoftware: () async {
          _isScreenVisible = false;
          _stopLiveUpdates();
          await Navigator.of(context).push(MaterialPageRoute<void>(
            builder: (_) => SoftwareSwitchScreen(
              state: widget.state,
              serverId: selectedServerId!,
              currentFlavor: detail!['flavor']?.toString() ?? 'paper',
            ),
          ));
          if (mounted) {
            _isScreenVisible = true;
            if (selectedServerId != null) _startLiveUpdates();
            _loadDetail(selectedServerId!); // Refresh to show new software
          }
        },
        onUpgrade: () async {
          debugPrint('Dashboard: onUpgrade triggered for server $selectedServerId');
          _isScreenVisible = false;
          _stopLiveUpdates();
          await Navigator.of(context).push(MaterialPageRoute<void>(
            builder: (_) => PlansScreen(
              state: widget.state,
              serverId: selectedServerId!,
              onPlanUpgraded: () {
                debugPrint('Dashboard: Plan upgraded callback fired');
                _loadDetail(selectedServerId!); // Refresh data
              },
            ),
          ));
          if (mounted) {
            _isScreenVisible = true;
            if (selectedServerId != null) _startLiveUpdates();
          }
        },
      );
    }

    final runningCount = servers.where((s) {
      final sMap = s as Map<String, dynamic>;
      if (sMap['state'] != 'running') return false;
      final ls = _listStats[sMap['id'] as String];
      if (ls != null && ls['process_running'] == false) return false;
      return true;
    }).length;

    return Scaffold(
      backgroundColor: _bg,
      body: SafeArea(
        child: Column(
          children: [
            // ── Top bar ────────────────────────────────────────────────────
            Container(
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 14),
              decoration: const BoxDecoration(
                border: Border(bottom: BorderSide(color: _border)),
              ),
              child: Row(
                children: [
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('Dashboard',
                          style: TextStyle(
                              color: _text,
                              fontSize: 18,
                              fontWeight: FontWeight.bold)),
                      Text(
                        busy
                            ? 'Loading...'
                            : '$runningCount active instance${runningCount == 1 ? '' : 's'} running.',
                        style: const TextStyle(
                            color: _muted, fontSize: 11, fontFamily: _mono),
                      ),
                    ],
                  ),
                  const Spacer(),
                  Stack(
                    children: [
                      Container(
                        width: 36,
                        height: 36,
                        decoration: BoxDecoration(
                          border: Border.all(color: _border),
                          borderRadius: BorderRadius.circular(18),
                          color: _surface,
                        ),
                        child: const Icon(Icons.person_outline_rounded,
                            color: _mutedBright, size: 18),
                      ),
                      if ((widget.state.accessToken ?? '').isNotEmpty)
                        Positioned(
                          right: 2,
                          bottom: 2,
                          child: Container(
                            width: 8,
                            height: 8,
                            decoration: const BoxDecoration(
                                color: _green, shape: BoxShape.circle),
                          ),
                        ),
                    ],
                  ),
                ],
              ),
            ),

            // ── Resource status bar ─────────────────────────────────────────
            if (status.isNotEmpty)
              Container(
                margin: const EdgeInsets.fromLTRB(20, 12, 20, 0),
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Colors.redAccent.withOpacity(.08),
                  border: Border.all(color: Colors.redAccent.withOpacity(.3)),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.warning_amber_rounded,
                        color: Colors.redAccent, size: 14),
                    const SizedBox(width: 8),
                    Expanded(
                        child: Text(status,
                            style: const TextStyle(
                                color: Colors.redAccent,
                                fontSize: 11,
                                fontFamily: _mono))),
                  ],
                ),
              ),

            Expanded(
              child: RefreshIndicator(
                color: _green,
                backgroundColor: _surface,
                onRefresh: () => _refresh(forceNetwork: true),
                child: busy && servers.isEmpty
                    ? const Center(
                        child: CircularProgressIndicator(
                            color: _green, strokeWidth: 1.5))
                    : servers.isEmpty
                        ? Center(
                            child: Column(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                const Icon(Icons.dns_outlined,
                                    color: _muted, size: 40),
                                const SizedBox(height: 10),
                                const Text(
                                    'No servers yet.\nCreate one to get started.',
                                    textAlign: TextAlign.center,
                                    style: TextStyle(
                                        color: _muted,
                                        fontSize: 13,
                                        fontFamily: _mono)),
                                const SizedBox(height: 24),
                                ElevatedButton.icon(
                                  onPressed: () {
                                    Navigator.of(context).push(
                                      MaterialPageRoute(
                                        builder: (_) => CreateServerScreen(
                                          state: widget.state,
                                          onCreated: () {
                                            Navigator.of(context).pop();
                                            _refresh();
                                          },
                                        ),
                                      ),
                                    );
                                  },
                                  style: ElevatedButton.styleFrom(
                                    backgroundColor: _green,
                                    foregroundColor: _bg,
                                    padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
                                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
                                  ),
                                  icon: const Icon(Icons.add_rounded, size: 18),
                                  label: const Text('Create New Server', style: TextStyle(fontWeight: FontWeight.bold, fontFamily: _mono)),
                                ),
                              ],
                            ),
                          )
                        : ListView.builder(
                            padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
                            itemCount: servers.length,
                            itemBuilder: (context, i) {
                              final s = servers[i] as Map<String, dynamic>;
                              return Padding(
                                padding: const EdgeInsets.only(bottom: 12),
                                child: _ServerCard(
                                  server: s,
                                  liveStats: _listStats[s['id'] as String],
                                  onToggle: () => _toggle(
                                    s['id'] as String,
                                    launchWhenRunning:
                                        (s['state'] as String? ?? '') !=
                                            'running',
                                    launchServer: s,
                                  ),
                                  onLaunch: () => _launchMinecraft(s),
                                  onTap: () => _loadDetail(s['id'] as String),
                                  busy: busy,
                                ),
                              );
                            },
                          ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ─── Server List Card ─────────────────────────────────────────────────────────

class _ServerCard extends StatelessWidget {
  const _ServerCard({
    required this.server,
    required this.onToggle,
    required this.onLaunch,
    required this.onTap,
    required this.busy,
    this.liveStats,
  });

  final Map<String, dynamic> server;
  final Map<String, dynamic>? liveStats;
  final VoidCallback onToggle;
  final VoidCallback onLaunch;
  final VoidCallback onTap;
  final bool busy;

  bool get _isRunning {
    if ((server['state'] as String? ?? '') != 'running') return false;
    if (liveStats != null && liveStats!['process_running'] == false)
      return false;
    return true;
  }

  bool get _isRestarting => (server['state'] as String? ?? '') == 'restarting';

  @override
  Widget build(BuildContext context) {
    final worldName =
        server['world_name']?.toString() ?? server['id'].toString();
    final address = server['shareable_address']?.toString() ?? 'N/A';
    final mcConfig = server['mc_config'] as Map<String, dynamic>?;
    final playersOnline = liveStats?['players_online'] ?? 0;
    final playersMax =
        liveStats?['players_max'] ?? mcConfig?['max_players'] ?? 10;
    final version = liveStats?['version']?.toString() ??
        mcConfig?['bedrock_version']?.toString() ??
        '';

    return GlassCard(
      isSelected: _isRunning,
      padding: EdgeInsets.zero,
      margin: const EdgeInsets.only(bottom: 16),
      onTap: onTap,
      child: Column(
          children: [
            // Header
            Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          worldName.toUpperCase(),
                          style: const TextStyle(
                            color: _text,
                            fontSize: 15,
                            fontWeight: FontWeight.bold,
                            fontFamily: _mono,
                            letterSpacing: 0.5,
                          ),
                        ),
                        const SizedBox(height: 5),
                        Row(children: [
                          const Icon(Icons.public_rounded,
                              size: 11, color: _muted),
                          const SizedBox(width: 4),
                          Text(
                            address.toUpperCase(),
                            style: const TextStyle(
                                color: _green, fontSize: 10, fontFamily: _mono),
                          ),
                        ]),
                      ],
                    ),
                  ),
                  _StatusBadge(
                      isRunning: _isRunning, isRestarting: _isRestarting),
                ],
              ),
            ),

            // Stats row
            Container(
              margin: const EdgeInsets.fromLTRB(16, 0, 16, 14),
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: _bg,
                borderRadius: BorderRadius.circular(3),
                border: Border.all(color: _border),
              ),
              child: Row(
                children: [
                  _InlineStatItem(
                      label: 'PLAYERS', value: '$playersOnline/$playersMax'),
                  if (version.isNotEmpty) ...[
                    _StatDivider(),
                    _InlineStatItem(label: 'VERSION', value: version),
                  ],
                  if (liveStats?['cpu_usage_percent'] != null) ...[
                    _StatDivider(),
                    _InlineStatItem(
                        label: 'CPU',
                        value: '${(liveStats!['cpu_usage_percent'] as num).toStringAsFixed(0)}%'),
                  ],
                  if (liveStats?['ram_usage_mb'] != null) ...[
                    _StatDivider(),
                    _InlineStatItem(
                        label: 'RAM',
                        value: '${(liveStats!['ram_usage_mb'] as num).toStringAsFixed(0)}MB'),
                  ],
                ],
              ),
            ),

            // Action bar
            Container(
              decoration: const BoxDecoration(
                border: Border(top: BorderSide(color: _border)),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: _ActionBarBtn(
                      icon: Icons.terminal_rounded,
                      label: 'Console',
                      onTap: onTap,
                    ),
                  ),
                  Container(width: 1, height: 40, color: _border),
                  if (_isRunning) ...[
                    Expanded(
                      child: _ActionBarBtn(
                        icon: Icons.sports_esports_rounded,
                        label: 'Join',
                        accent: true,
                        onTap: busy ? null : onLaunch,
                      ),
                    ),
                    Container(width: 1, height: 40, color: _border),
                  ],
                  SizedBox(
                    width: 56,
                    height: 44,
                    child: _isRestarting
                        ? const Center(
                            child: SizedBox(
                              width: 14,
                              height: 14,
                              child: CircularProgressIndicator(
                                  strokeWidth: 1.5, color: _muted),
                            ),
                          )
                        : IconButton(
                            onPressed: busy ? null : onToggle,
                            icon: Icon(
                              _isRunning
                                  ? Icons.power_settings_new_rounded
                                  : Icons.play_arrow_rounded,
                              color: _isRunning ? Colors.redAccent : _green,
                              size: 20,
                            ),
                          ),
                  ),
                ],
              ),
            ),
          ],
        ),
    );
  }
}

List<String> _extractOnlinePlayerNames(dynamic players) {
  if (players == null) return [];
  if (players is List) {
    return players
        .map((p) {
          if (p is Map) {
            return p['name']?.toString() ?? '';
          }
          return p?.toString() ?? '';
        })
        .where((name) => name.isNotEmpty)
        .toList();
  }
  return [];
}

// ─── Server Detail View ───────────────────────────────────────────────────────

class _ServerDetailView extends StatelessWidget {
  const _ServerDetailView({
    required this.detail,
    required this.stats,
    required this.subscription,
    required this.busy,
    required this.onBack,
    required this.onToggle,
    required this.onLaunch,
    required this.onOpenConsole,
    required this.onOpenSettings,
    required this.onOpenBackups,
    required this.onSwitchSoftware,
    required this.onUpgrade,
  });

  final Map<String, dynamic> detail;
  final Map<String, dynamic>? stats;
  final Map<String, dynamic>? subscription;
  final bool busy;
  final VoidCallback onBack;
  final VoidCallback onToggle;
  final VoidCallback onLaunch;
  final VoidCallback onOpenConsole;
  final VoidCallback onOpenSettings;
  final VoidCallback onOpenBackups;
  final VoidCallback onSwitchSoftware;
  final VoidCallback onUpgrade;

  bool get _isRunning {
    if ((detail['state'] as String? ?? '') != 'running') return false;
    if (stats != null && stats!['process_running'] == false) return false;
    return true;
  }

  String _formatUptime(num? seconds) {
    if (seconds == null) return '—';
    final s = seconds.toInt();
    final h = s ~/ 3600;
    final m = (s % 3600) ~/ 60;
    final sec = s % 60;
    if (h > 0) return '${h}H ${m}M';
    if (m > 0) return '${m}M ${sec}S';
    return '${sec}S';
  }

  @override
  Widget build(BuildContext context) {
    final worldName = detail['world_name']?.toString() ?? 'Server';
    final address = detail['shareable_address']?.toString() ?? 'N/A';
    final mcConfig = detail['mc_config'] as Map<String, dynamic>?;
    final playersOnline = stats?['players_online'] ?? 0;
    final playersMax = stats?['players_max'] ?? mcConfig?['max_players'] ?? 10;
    final cpuUsage = stats?['cpu_usage_percent'];
    final ramUsageMb = stats?['ram_usage_mb'];
    final version = stats?['version']?.toString() ?? '';
    final gamemode = stats?['gamemode']?.toString() ??
        mcConfig?['gamemode']?.toString() ??
        '';
    final onlinePlayers = _extractOnlinePlayerNames(stats?['online_players_list']);
    final uptime = stats?['uptime_seconds'] as num?;
    final tps = stats?['tps'];
    final latency = stats?['latency_ms'];
    final ramPct = stats?['ram_usage_percent'];
    final cpuStr =
        cpuUsage != null ? '${(cpuUsage as num).toStringAsFixed(0)}%' : 'N/A';
    final ramStr = ramUsageMb != null
        ? '${(ramUsageMb / 1024.0).toStringAsFixed(1)} GB'
        : 'N/A';

    return Scaffold(
      backgroundColor: _bg,
      body: SafeArea(
        child: Column(
          children: [
            // Top bar
            Container(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
              decoration: const BoxDecoration(
                border: Border(bottom: BorderSide(color: _border)),
              ),
              child: Row(
                children: [
                  GestureDetector(
                    onTap: onBack,
                    child: const Row(
                      children: [
                        Icon(Icons.arrow_back_rounded,
                            color: _mutedBright, size: 18),
                        SizedBox(width: 6),
                        Text('BACK',
                            style: TextStyle(
                                color: _muted,
                                fontSize: 11,
                                fontFamily: _mono)),
                      ],
                    ),
                  ),
                  const Spacer(),
                  if (_isRunning)
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 4),
                      decoration: BoxDecoration(
                        border: Border.all(color: _greenDim),
                        borderRadius: BorderRadius.circular(2),
                      ),
                      child: const Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          _PulsingDot(),
                          SizedBox(width: 6),
                          Text('LIVE',
                              style: TextStyle(
                                  color: _green,
                                  fontSize: 10,
                                  fontFamily: _mono,
                                  letterSpacing: 1.5)),
                        ],
                      ),
                    ),
                  const Spacer(),
                  GestureDetector(
                    onTap: () {
                      Clipboard.setData(ClipboardData(text: address));
                      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                        backgroundColor: _surface,
                        content: Text('Address copied!',
                            style: TextStyle(
                                color: _text, fontFamily: _mono, fontSize: 12)),
                      ));
                    },
                    child: const Icon(Icons.share_rounded,
                        color: _mutedBright, size: 20),
                  ),
                ],
              ),
            ),

            Expanded(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    // ── Server identity ──────────────────────────────────────
                    Text(
                      worldName.toUpperCase(),
                      style: const TextStyle(
                          color: _text,
                          fontSize: 22,
                          fontWeight: FontWeight.bold,
                          fontFamily: _mono),
                    ),
                    const SizedBox(height: 4),
                    Row(children: [
                      _StatusBadge(isRunning: _isRunning),
                      const SizedBox(width: 10),
                      Text(address,
                          style: const TextStyle(
                              color: _muted, fontSize: 11, fontFamily: _mono)),
                    ]),

                    const SizedBox(height: 28),


                    // ── Power button ─────────────────────────────────────────
                    _GlowingPowerButton(
                      isRunning: _isRunning,
                      busy: busy,
                      onToggle: onToggle,
                    ),
                    const SizedBox(height: 10),
                    Center(
                      child: Text(
                        _isRunning
                            ? 'RUNNING — ${_formatUptime(uptime)}'
                            : 'STOPPED',
                        style: TextStyle(
                          color: _isRunning ? _green : _muted,
                          fontSize: 11,
                          fontFamily: _mono,
                          letterSpacing: 2,
                        ),
                      ),
                    ),

                    if (_isRunning) ...[
                      const SizedBox(height: 16),
                      _GreenOutlineButton(
                        icon: Icons.sports_esports_rounded,
                        label: 'Launch Minecraft',
                        onTap: busy ? null : onLaunch,
                      ),
                    ],

                    if (subscription == null || subscription?['status'] != 'active') ...[
                      const SizedBox(height: 16),
                      GlowingButton(
                        icon: Icons.workspace_premium_rounded,
                        label: 'Upgrade Server',
                        onTap: () {
                          debugPrint('ServerDetailView: Upgrade Server button clicked! busy=$busy');
                          if (busy) {
                            debugPrint('ServerDetailView: onTap ignored because busy is true');
                            return;
                          }
                          onUpgrade();
                        },
                      ),
                    ],

                    const SizedBox(height: 20),

                    // ── Quick stats chips ────────────────────────────────────
                    SingleChildScrollView(
                      scrollDirection: Axis.horizontal,
                      child: Row(
                        children: [
                          _Chip(
                              icon: Icons.people_rounded,
                              label: '$playersOnline/$playersMax Players'),
                          if (latency != null)
                            _Chip(
                                icon: Icons.speed_rounded,
                                label: '${latency}ms Ping'),
                          if (tps != null)
                            _Chip(
                                icon: Icons.timeline_rounded,
                                label:
                                    'TPS ${(tps as num).toStringAsFixed(1)}'),
                          if (version.isNotEmpty)
                            _Chip(
                                icon: Icons.info_outline_rounded,
                                label: version),
                          if (gamemode.isNotEmpty)
                            _Chip(
                                icon: Icons.videogame_asset_rounded,
                                label: gamemode),
                        ],
                      ),
                    ),

                    if (onlinePlayers.isNotEmpty) ...[
                      const SizedBox(height: 10),
                      Wrap(
                        spacing: 8,
                        runSpacing: 6,
                        children: onlinePlayers
                            .map((p) => Container(
                                  padding: const EdgeInsets.symmetric(
                                      horizontal: 10, vertical: 5),
                                  decoration: BoxDecoration(
                                    color: _green.withOpacity(.08),
                                    border: Border.all(
                                        color: _green.withOpacity(.25)),
                                    borderRadius: BorderRadius.circular(2),
                                  ),
                                  child: Row(
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                        const Icon(Icons.person,
                                            size: 12, color: _green),
                                        const SizedBox(width: 5),
                                        Text(p,
                                            style: const TextStyle(
                                                color: _text,
                                                fontSize: 11,
                                                fontFamily: _mono)),
                                      ]),
                                ))
                            .toList(),
                      ),
                    ],

                    const SizedBox(height: 16),

                    // ── CPU / RAM ─────────────────────────────────────────────
                    _StatProgressCard(
                      title: 'CPU USAGE',
                      subtitle: 'Core Usage',
                      percent: cpuUsage != null ? ((cpuUsage as num) / 100).clamp(0.0, 1.0) : 0.0,
                      displayValue: cpuStr,
                    ),
                    const SizedBox(height: 12),
                    _StatProgressCard(
                      title: 'RAM USAGE',
                      subtitle: ramUsageMb != null ? '${(ramUsageMb / 1024).toStringAsFixed(1)} GB' : 'N/A',
                      percent: ramPct != null ? ((ramPct as num) / 100).clamp(0.0, 1.0) : 0.0,
                      displayValue: ramPct != null ? '${(ramPct as num).toStringAsFixed(0)}%' : '0%',
                    ),

                    const SizedBox(height: 16),

                    // ── Tabs: Console / Backups / Settings ─────────────────────
                    Row(children: [
                      Expanded(
                          child: _TabBtn(
                              icon: Icons.terminal_rounded,
                              label: 'Console',
                              onTap: onOpenConsole)),
                      const SizedBox(width: 8),
                      Expanded(
                          child: _TabBtn(
                              icon: Icons.cloud_queue_rounded,
                              label: 'Backups',
                              onTap: onOpenBackups)),
                      const SizedBox(width: 8),
                      Expanded(
                          child: _TabBtn(
                              icon: Icons.settings_rounded,
                              label: 'Settings',
                              selected: true,
                              onTap: onOpenSettings)),
                    ]),

                    const SizedBox(height: 14),
                    
                    Container(
                      padding: const EdgeInsets.all(16),
                      decoration: BoxDecoration(
                        color: const Color(0xFF050505),
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(color: _border),
                      ),
                      child: Row(
                        children: [
                          Icon(
                            Icons.apps_rounded,
                            color: _green,
                            size: 20,
                          ),
                          const SizedBox(width: 12),
                          const Expanded(
                            child: Text(
                              'Switch server software and version safely with automatic backups.',
                              style: TextStyle(
                                  color: _mutedBright,
                                  fontSize: 11,
                                  fontFamily: _mono,
                                  height: 1.4),
                            ),
                          ),
                          const SizedBox(width: 12),
                          TextButton(
                            onPressed: onSwitchSoftware,
                            child: const Text('SWITCH'),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 14),

                    Container(
                      padding: const EdgeInsets.all(16),
                      decoration: BoxDecoration(
                        color: const Color(0xFF050505),
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(color: _border),
                      ),
                      child: Row(
                        children: [
                          Icon(
                            Icons.terminal_rounded,
                            color: _isRunning ? _green : _muted,
                            size: 20,
                          ),
                          const SizedBox(width: 12),
                          const Expanded(
                            child: Text(
                              'Console opens a live WebSocket stream only when you request it.',
                              style: TextStyle(
                                  color: _mutedBright,
                                  fontSize: 11,
                                  fontFamily: _mono,
                                  height: 1.4),
                            ),
                          ),
                          const SizedBox(width: 12),
                          TextButton(
                            onPressed: _isRunning ? onOpenConsole : null,
                            child: const Text('OPEN'),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 20),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ─── Shared small widgets ─────────────────────────────────────────────────────

class _StatusBadge extends StatelessWidget {
  const _StatusBadge({required this.isRunning, this.isRestarting = false});
  final bool isRunning;
  final bool isRestarting;

  @override
  Widget build(BuildContext context) {
    final color = isRestarting
        ? Colors.orangeAccent
        : isRunning
            ? _green
            : _muted;
    final label = isRestarting
        ? 'RESTARTING'
        : isRunning
            ? 'ONLINE'
            : 'OFFLINE';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        color: color.withOpacity(.1),
        border: Border.all(color: color.withOpacity(.4)),
        borderRadius: BorderRadius.circular(2),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Container(
            width: 6,
            height: 6,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
        const SizedBox(width: 5),
        Text(label,
            style: TextStyle(
                color: color,
                fontSize: 10,
                fontFamily: _mono,
                fontWeight: FontWeight.bold,
                letterSpacing: 0.5)),
      ]),
    );
  }
}

class _InlineStatItem extends StatelessWidget {
  const _InlineStatItem({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: const TextStyle(
                  color: _muted,
                  fontSize: 9,
                  fontFamily: _mono,
                  letterSpacing: 1)),
          const SizedBox(height: 2),
          Text(value,
              style: const TextStyle(
                  color: _text,
                  fontSize: 14,
                  fontWeight: FontWeight.bold,
                  fontFamily: _mono)),
        ],
      );
}

class _StatDivider extends StatelessWidget {
  @override
  Widget build(BuildContext context) => Container(
      width: 1,
      height: 28,
      color: _border,
      margin: const EdgeInsets.symmetric(horizontal: 14));
}

class _ActionBarBtn extends StatelessWidget {
  const _ActionBarBtn(
      {required this.icon,
      required this.label,
      required this.onTap,
      this.accent = false});
  final IconData icon;
  final String label;
  final VoidCallback? onTap;
  final bool accent;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: Container(
          height: 44,
          alignment: Alignment.center,
          child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(icon, size: 14, color: accent ? _green : _muted),
            const SizedBox(width: 6),
            Text(label,
                style: TextStyle(
                    color: accent ? _green : _muted,
                    fontSize: 12,
                    fontFamily: _mono)),
          ]),
        ),
      );
}

class _GreenOutlineButton extends StatelessWidget {
  const _GreenOutlineButton(
      {required this.icon, required this.label, required this.onTap});
  final IconData icon;
  final String label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: Container(
          height: 50,
          decoration: BoxDecoration(
            color: _green,
            borderRadius: BorderRadius.circular(4),
          ),
          child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(icon, color: _bg, size: 18),
            const SizedBox(width: 8),
            Text(label,
                style: const TextStyle(
                    color: _bg,
                    fontWeight: FontWeight.w800,
                    fontSize: 14,
                    fontFamily: _mono)),
          ]),
        ),
      );
}

class _Chip extends StatelessWidget {
  const _Chip({required this.icon, required this.label});
  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.only(right: 8),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: _surface,
          border: Border.all(color: _border),
          borderRadius: BorderRadius.circular(2),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Icon(icon, size: 12, color: _green),
          const SizedBox(width: 5),
          Text(label,
              style: const TextStyle(
                  color: _mutedBright, fontSize: 11, fontFamily: _mono)),
        ]),
      );
}

class _MetricBlock extends StatelessWidget {
  const _MetricBlock(
      {required this.label,
      required this.value,
      required this.progress,
      required this.barColor});
  final String label;
  final String value;
  final double progress;
  final Color barColor;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: _surface,
          border: Border.all(color: _border),
          borderRadius: BorderRadius.circular(4),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label,
              style: const TextStyle(
                  color: _muted,
                  fontSize: 10,
                  fontFamily: _mono,
                  letterSpacing: 1)),
          const SizedBox(height: 6),
          Text(value,
              style: const TextStyle(
                  color: _text,
                  fontSize: 22,
                  fontWeight: FontWeight.bold,
                  fontFamily: _mono)),
          const SizedBox(height: 10),
          ClipRRect(
            borderRadius: BorderRadius.circular(2),
            child: LinearProgressIndicator(
              value: progress,
              backgroundColor: _border,
              valueColor: AlwaysStoppedAnimation<Color>(barColor),
              minHeight: 3,
            ),
          ),
        ]),
      );
}

class _TabBtn extends StatelessWidget {
  const _TabBtn(
      {required this.icon,
      required this.label,
      required this.onTap,
      this.selected = false});
  final IconData icon;
  final String label;
  final VoidCallback? onTap;
  final bool selected;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: Container(
          height: 44,
          decoration: BoxDecoration(
            color: selected ? _green.withOpacity(.1) : _surface,
            border: Border.all(color: selected ? _greenDim : _border),
            borderRadius: BorderRadius.circular(4),
          ),
          child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(icon, size: 14, color: selected ? _green : _muted),
            const SizedBox(width: 6),
            Text(label,
                style: TextStyle(
                    color: selected ? _green : _muted,
                    fontSize: 11,
                    fontFamily: _mono)),
          ]),
        ),
      );
}

class _PulsingDot extends StatefulWidget {
  const _PulsingDot();

  @override
  State<_PulsingDot> createState() => _PulsingDotState();
}

class _PulsingDotState extends State<_PulsingDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 900))
      ..repeat(reverse: true);
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => FadeTransition(
        opacity: Tween(begin: 0.3, end: 1.0).animate(_ctrl),
        child: Container(
            width: 6,
            height: 6,
            decoration:
                const BoxDecoration(color: _green, shape: BoxShape.circle)),
      );
}

class _StatProgressCard extends StatelessWidget {
  const _StatProgressCard({
    required this.title,
    required this.subtitle,
    required this.percent,
    required this.displayValue,
  });

  final String title;
  final String subtitle;
  final double percent;
  final String displayValue;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: const Color(0xFF161622),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withOpacity(0.05)),
      ),
      child: Row(
        children: [
          SizedBox(
            width: 70,
            height: 70,
            child: Stack(
              children: [
                CustomPaint(
                  size: const Size(70, 70),
                  painter: _RingPainter(percent: percent),
                ),
                Center(
                  child: Text(
                    displayValue,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 18,
                      fontWeight: FontWeight.bold,
                      fontFamily: _mono,
                    ),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: 24),
          Expanded(
            child: Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                border: Border.all(
                  color: Colors.indigo.withOpacity(0.3),
                  width: 1.5,
                ),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: const TextStyle(
                      color: _muted,
                      fontSize: 10,
                      fontWeight: FontWeight.bold,
                      letterSpacing: 1.0,
                      fontFamily: _mono,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    subtitle,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 15,
                      fontFamily: _mono,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}


class _RingPainter extends CustomPainter {
  _RingPainter({required this.percent});
  final double percent;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = size.width / 2 - 4;

    final bgPaint = Paint()
      ..color = Colors.white.withOpacity(0.1)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 6;
    canvas.drawCircle(center, radius, bgPaint);

    final fgPaint = Paint()
      ..shader = SweepGradient(
        colors: const [Color(0xFF0284C7), _green, Color(0xFF0284C7)],
        stops: const [0.0, 0.5, 1.0],
      ).createShader(Rect.fromCircle(center: center, radius: radius))
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeWidth = 6;

    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      -pi / 2,
      2 * pi * percent,
      false,
      fgPaint,
    );
  }

  @override
  bool shouldRepaint(covariant _RingPainter oldDelegate) {
    return oldDelegate.percent != percent;
  }
}

class _GlowingPowerButton extends StatelessWidget {
  const _GlowingPowerButton({
    required this.isRunning,
    required this.busy,
    required this.onToggle,
  });
  final bool isRunning;
  final bool busy;
  final VoidCallback onToggle;
  
  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: busy ? null : onToggle,
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(vertical: 16),
        decoration: BoxDecoration(
          gradient: LinearGradient(
            colors: isRunning 
                ? [const Color(0xFFEF4444), const Color(0xFFB91C1C)]
                : [const Color(0xFF06B6D4), const Color(0xFF0284C7)],
          ),
          borderRadius: BorderRadius.circular(32),
          boxShadow: [
            BoxShadow(
              color: isRunning 
                  ? const Color(0xFFEF4444).withOpacity(0.3)
                  : const Color(0xFF06B6D4).withOpacity(0.3),
              blurRadius: 16,
              offset: const Offset(0, 8),
            ),
          ],
        ),
        child: busy 
          ? const Center(child: SizedBox(height: 20, width: 20, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2)))
          : Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Icon(Icons.power_settings_new_rounded, color: Colors.white, size: 20),
                const SizedBox(width: 8),
                Text(
                  isRunning ? 'STOP SERVER' : 'START SERVER',
                  style: const TextStyle(
                      color: Colors.white,
                      fontSize: 14,
                      fontWeight: FontWeight.bold,
                      fontFamily: _mono,
                      letterSpacing: 0.5),
                ),
              ],
            ),
      ),
    );
  }
}
