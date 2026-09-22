import 'dart:async';
import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../models/database_models.dart';
import '../../models/deployment_models.dart';
import '../../state/app_state.dart';
import '../widgets/glass_card.dart';
import 'deployment_console_screen.dart';
import 'deployment_env_screen.dart';
import 'deployment_backups_screen.dart';
import 'deployment_metrics_screen.dart';

class DeploymentDetailScreen extends StatefulWidget {
  const DeploymentDetailScreen({super.key, required this.state, required this.deploymentId});
  final AppState state;
  final String deploymentId;

  @override
  State<DeploymentDetailScreen> createState() => _DeploymentDetailScreenState();
}

class _DeploymentDetailScreenState extends State<DeploymentDetailScreen> {
  AppDeployment? _deployment;
  List<String> _logs = [];
  bool _isLoading = true;
  Timer? _logTimer;

  final _domainCtrl = TextEditingController();
  bool _attachingDomain = false;

  Map<String, String> _envVars = {};
  final _envKeyCtrl = TextEditingController();
  final _envValCtrl = TextEditingController();
  bool _savingEnvVars = false;
  List<DatabaseInstance> _databases = [];
  String? _selectedDatabaseId;
  bool _connectingDatabase = false;
  
  List<CustomDomain> _customDomains = [];
  bool _verifyingDomain = false;

  @override
  void initState() {
    super.initState();
    _loadAll();
    _logTimer = Timer.periodic(const Duration(seconds: 5), (_) => _pollLogs());
  }

  @override
  void dispose() {
    _logTimer?.cancel();
    _domainCtrl.dispose();
    _envKeyCtrl.dispose();
    _envValCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadAll() async {
    try {
      final deps = await widget.state.api.getDeployments();
      final dep = deps.firstWhere((d) => d.id == widget.deploymentId);
      setState(() {
        _deployment = dep;
        _isLoading = false;
      });
      await _pollLogs();
      await _loadEnvVars();
      await _loadDatabases();
      await _loadDomains();
    } catch (e) {
      if (mounted) {
        setState(() => _isLoading = false);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error loading deployment: $e')));
      }
    }
  }

  Future<void> _loadDatabases() async {
    try {
      final databases = await widget.state.api.getDatabases();
      if (mounted) {
        setState(() {
          _databases = databases
              .where((db) => _deployment?.projectId == null || db.projectId == _deployment!.projectId)
              .toList();
          if (_selectedDatabaseId == null && _databases.isNotEmpty) {
            _selectedDatabaseId = _databases.first.id;
          }
        });
      }
    } catch (e) {
      // Database attachment is optional on the app detail screen.
    }
  }

  Future<void> _connectSelectedDatabase() async {
    final dep = _deployment;
    final databaseId = _selectedDatabaseId;
    if (dep == null || dep.projectId == null || databaseId == null) return;

    setState(() => _connectingDatabase = true);
    try {
      await widget.state.api.connectDatabaseToApp(
        projectId: dep.projectId!,
        databaseId: databaseId,
        appId: dep.id,
      );
      await _loadEnvVars();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Database connected. App redeploy triggered.')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error connecting database: $e')));
      }
    } finally {
      if (mounted) setState(() => _connectingDatabase = false);
    }
  }

  Future<void> _pollLogs() async {
    if (_deployment == null || !_deployment!.isRunning) return;
    try {
      final logs = await widget.state.api.getDeploymentLogs(widget.deploymentId, tail: 50);
      if (mounted) {
        setState(() => _logs = logs);
      }
    } catch (e) {
      // Ignore poll errors to avoid spam
    }
  }

  Future<void> _start() async {
    if (_deployment == null) return;
    try {
      final dep = await widget.state.api.startDeployment(widget.deploymentId);
      setState(() => _deployment = dep);
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error starting: $e')));
    }
  }

  Future<void> _stop() async {
    if (_deployment == null) return;
    try {
      final dep = await widget.state.api.stopDeployment(widget.deploymentId);
      setState(() => _deployment = dep);
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error stopping: $e')));
    }
  }

  Future<void> _attachDomain() async {
    final domain = _domainCtrl.text.trim();
    if (domain.isEmpty) return;

    setState(() => _attachingDomain = true);
    try {
      await widget.state.api.attachDomain(widget.deploymentId, domain);
      _domainCtrl.clear();
      await _loadAll(); // reload to get new domains
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error attaching domain: $e')));
    } finally {
      if (mounted) setState(() => _attachingDomain = false);
    }
  }

  Future<void> _loadEnvVars() async {
    try {
      final env = await widget.state.api.getEnvVars(widget.deploymentId, decrypt: true);
      if (mounted) setState(() => _envVars = env);
    } catch (e) {
      // ignore
    }
  }

  Future<void> _loadDomains() async {
    try {
      final domains = await widget.state.api.getCustomDomains(widget.deploymentId);
      if (mounted) setState(() => _customDomains = domains);
    } catch (e) {
      // ignore
    }
  }

  Future<void> _verifyDomain(String domainId) async {
    setState(() => _verifyingDomain = true);
    try {
      await widget.state.api.verifyCustomDomain(domainId);
      await _loadDomains();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Domain verified successfully!'), backgroundColor: Colors.green));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Verification failed: $e')));
      }
    } finally {
      if (mounted) setState(() => _verifyingDomain = false);
    }
  }

  Future<void> _removeDomain(String domainId) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF1E293B),
        title: const Text('Remove Domain?', style: TextStyle(color: Colors.white)),
        content: const Text('Are you sure you want to remove this custom domain? Traffic will stop routing immediately.', style: TextStyle(color: Colors.white70)),
        actions: [
          TextButton(onPressed: () => Navigator.of(ctx).pop(false), child: const Text('Cancel', style: TextStyle(color: Colors.white54))),
          TextButton(onPressed: () => Navigator.of(ctx).pop(true), child: const Text('Remove', style: TextStyle(color: Colors.redAccent))),
        ],
      ),
    );
    if (confirm != true) return;

    try {
      await widget.state.api.removeCustomDomain(domainId);
      await _loadDomains();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Domain removed.')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error removing domain: $e')));
      }
    }
  }

  Future<void> _saveEnvVars(Map<String, String> newEnv) async {
    setState(() => _savingEnvVars = true);
    try {
      await widget.state.api.updateEnvVars(widget.deploymentId, newEnv);
      setState(() => _envVars = newEnv);
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Environment variables saved! Restart to apply.')));
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error saving env vars: $e')));
    } finally {
      if (mounted) setState(() => _savingEnvVars = false);
    }
  }

  void _addEnvVar() {
    final k = _envKeyCtrl.text.trim();
    final v = _envValCtrl.text.trim();
    if (k.isEmpty || v.isEmpty) return;

    final newEnv = Map<String, String>.from(_envVars);
    newEnv[k] = v;
    _saveEnvVars(newEnv).then((_) {
      _envKeyCtrl.clear();
      _envValCtrl.clear();
    });
  }

  void _removeEnvVar(String k) {
    final newEnv = Map<String, String>.from(_envVars);
    newEnv.remove(k);
    _saveEnvVars(newEnv);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent, // Assumes parent has the background
      appBar: AppBar(
        title: Text(_deployment?.name ?? 'Loading...'),
        backgroundColor: Colors.transparent,
        elevation: 0,
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator(color: Color(0xFF06B6D4)))
          : _deployment == null
              ? const Center(child: Text('Deployment not found', style: TextStyle(color: Colors.white)))
              : SingleChildScrollView(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      _buildHeader(),
                      const SizedBox(height: 16),
                      _buildLogs(),
                      const SizedBox(height: 16),
                      _buildEnvVars(),
                      const SizedBox(height: 16),
                      if (_deployment?.volumeMountPath != null) ...[
                        _buildBackups(),
                        const SizedBox(height: 16),
                      ],
                      _buildDatabaseConnection(),
                      const SizedBox(height: 16),
                      _buildDomains(),
                    ],
                  ),
                ),
    );
  }

  Widget _buildHeader() {
    final dep = _deployment!;
    final isRunning = dep.isRunning;

    return GlassCard(
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          children: [
            Row(
              children: [
                Container(
                  width: 48,
                  height: 48,
                  decoration: BoxDecoration(
                    color: const Color(0xFF06B6D4).withOpacity(0.1),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: const Icon(Icons.widgets_outlined, color: Color(0xFF06B6D4)),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(dep.dockerImage, style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
                      const SizedBox(height: 4),
                      Text('Internal Port: ${dep.internalPort}', style: const TextStyle(color: Colors.white70, fontSize: 12)),
                      Text('RAM Limit: ${dep.ramLimitMb} MB', style: const TextStyle(color: Colors.white70, fontSize: 12)),
                      if (dep.publicUrl != null) ...[
                        const SizedBox(height: 8),
                        InkWell(
                          onTap: () => launchUrl(Uri.parse(dep.publicUrl!)),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              const Icon(Icons.open_in_new, color: Color(0xFF06B6D4), size: 14),
                              const SizedBox(width: 4),
                              Text(dep.publicUrl!, style: const TextStyle(color: Color(0xFF06B6D4), fontSize: 12, decoration: TextDecoration.underline)),
                            ],
                          ),
                        ),
                      ]
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: ElevatedButton(
                    onPressed: isRunning ? null : _start,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.greenAccent.withOpacity(0.2),
                      foregroundColor: Colors.greenAccent,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                    ),
                    child: const Text('Start'),
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: ElevatedButton(
                    onPressed: isRunning ? _stop : null,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.redAccent.withOpacity(0.2),
                      foregroundColor: Colors.redAccent,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                    ),
                    child: const Text('Stop'),
                  ),
                ),
              ],
            )
          ],
        ),
      ),
    );
  }

  Widget _buildLogs() {
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text('Container Logs', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
                if (_deployment != null)
                  Row(
                    children: [
                      TextButton.icon(
                        onPressed: () {
                          Navigator.of(context).push(MaterialPageRoute(
                            builder: (ctx) => DeploymentMetricsScreen(
                              state: widget.state,
                              deployment: _deployment!,
                            ),
                          ));
                        },
                        icon: const Icon(Icons.analytics_outlined, size: 14, color: Colors.purpleAccent),
                        label: const Text('Live Metrics', style: TextStyle(color: Colors.purpleAccent, fontSize: 12)),
                      ),
                      TextButton.icon(
                        onPressed: () {
                          Navigator.of(context).push(MaterialPageRoute(
                            builder: (ctx) => DeploymentConsoleScreen(
                              state: widget.state,
                              deploymentId: widget.deploymentId,
                              appName: _deployment!.name,
                            ),
                          ));
                        },
                        icon: const Icon(Icons.open_in_new, size: 14, color: Color(0xFF06B6D4)),
                        label: const Text('Live Stream', style: TextStyle(color: Color(0xFF06B6D4), fontSize: 12)),
                      ),
                    ],
                  ),
              ],
            ),
          ),
          Container(
            height: 250,
            margin: const EdgeInsets.fromLTRB(16, 0, 16, 16),
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(
              color: Colors.black.withOpacity(0.5),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: Colors.white.withOpacity(0.1)),
            ),
            child: _logs.isEmpty
                ? const Center(child: Text('No logs available.', style: TextStyle(color: Colors.white30, fontSize: 12)))
                : ListView.builder(
                    itemCount: _logs.length,
                    itemBuilder: (context, index) {
                      return Text(
                        _logs[index],
                        style: const TextStyle(color: Colors.white70, fontSize: 11, fontFamily: 'monospace'),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }

  Widget _buildEnvVars() {
    return GlassCard(
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text('Environment Variables', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
                TextButton.icon(
                  onPressed: () async {
                    final updated = await Navigator.of(context).push(MaterialPageRoute(
                      builder: (ctx) => DeploymentEnvScreen(
                        state: widget.state,
                        deploymentId: widget.deploymentId,
                      ),
                    ));
                    if (updated == true) {
                      _loadAll();
                    }
                  },
                  icon: const Icon(Icons.edit, size: 14, color: Color(0xFF06B6D4)),
                  label: const Text('Edit Variables', style: TextStyle(color: Color(0xFF06B6D4), fontSize: 12)),
                ),
              ],
            ),
            const SizedBox(height: 16),
            if (_envVars.isNotEmpty)
              ..._envVars.entries.map((e) {
                return Padding(
                  padding: const EdgeInsets.only(bottom: 8.0),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text('${e.key}=${e.value.length > 30 ? e.value.substring(0, 30) + "..." : e.value}',
                          style: const TextStyle(color: Colors.white70, fontSize: 13, fontFamily: 'monospace')),
                      ),
                      IconButton(
                        icon: const Icon(Icons.delete_outline, color: Colors.redAccent, size: 18),
                        onPressed: () => _removeEnvVar(e.key),
                      ),
                    ],
                  ),
                );
              }),
            if (_envVars.isEmpty)
              const Text('No environment variables set.', style: TextStyle(color: Colors.white30, fontSize: 12)),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  flex: 1,
                  child: TextField(
                    controller: _envKeyCtrl,
                    style: const TextStyle(color: Colors.white),
                    decoration: _inputDeco('Key (e.g. PORT)'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  flex: 2,
                  child: TextField(
                    controller: _envValCtrl,
                    style: const TextStyle(color: Colors.white),
                    decoration: _inputDeco('Value'),
                  ),
                ),
                const SizedBox(width: 8),
                ElevatedButton(
                  onPressed: _savingEnvVars ? null : _addEnvVar,
                  style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF06B6D4)),
                  child: _savingEnvVars
                      ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.black))
                      : const Text('Add', style: TextStyle(color: Colors.black)),
                ),
              ],
            )
          ],
        ),
      ),
    );
  }

  InputDecoration _inputDeco(String hint) {
    return InputDecoration(
      hintText: hint,
      hintStyle: const TextStyle(color: Colors.white30),
      filled: true,
      fillColor: Colors.black.withOpacity(0.2),
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 0),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: Colors.white.withOpacity(0.1))),
      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: Colors.white.withOpacity(0.1))),
      focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: const BorderSide(color: Color(0xFF06B6D4))),
    );
  }

  Widget _buildBackups() {
    return GlassCard(
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text('Database Backups', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
                TextButton.icon(
                  onPressed: () {
                    Navigator.of(context).push(MaterialPageRoute(
                      builder: (ctx) => DeploymentBackupsScreen(
                        state: widget.state,
                        deployment: _deployment!,
                      ),
                    ));
                  },
                  icon: const Icon(Icons.settings_backup_restore, size: 14, color: Color(0xFF06B6D4)),
                  label: const Text('Manage Backups', style: TextStyle(color: Color(0xFF06B6D4), fontSize: 12)),
                ),
              ],
            ),
            const SizedBox(height: 8),
            const Text(
              'Take manual snapshots of your database volume and restore them at any time to recover data.',
              style: TextStyle(color: Colors.white54, fontSize: 12),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildDatabaseConnection() {
    final dep = _deployment!;
    return GlassCard(
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Database Connection', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
            const SizedBox(height: 16),
            if (dep.projectId == null)
              const Text('This app is not assigned to a project.', style: TextStyle(color: Colors.white30, fontSize: 12))
            else if (_databases.isEmpty)
              const Text('No databases in this project yet.', style: TextStyle(color: Colors.white30, fontSize: 12))
            else
              Row(
                children: [
                  Expanded(
                    child: DropdownButtonFormField<String>(
                      value: _selectedDatabaseId,
                      dropdownColor: const Color(0xFF161622),
                      decoration: _inputDeco('Select database'),
                      items: _databases.map((db) {
                        return DropdownMenuItem(
                          value: db.id,
                          child: Text('${db.name} (${db.engine})', style: const TextStyle(color: Colors.white)),
                        );
                      }).toList(),
                      onChanged: (v) => setState(() => _selectedDatabaseId = v),
                    ),
                  ),
                  const SizedBox(width: 8),
                  ElevatedButton(
                    onPressed: _connectingDatabase ? null : _connectSelectedDatabase,
                    style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF06B6D4)),
                    child: _connectingDatabase
                        ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.black))
                        : const Text('Connect', style: TextStyle(color: Colors.black)),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildDomains() {
    return GlassCard(
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Custom Domains', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
            const SizedBox(height: 16),
            if (_customDomains.isNotEmpty)
              ..._customDomains.map((cd) {
                final bool isActive = cd.status == 'active';
                final bool isPending = cd.status == 'pending_dns';
                
                return Container(
                  margin: const EdgeInsets.only(bottom: 12.0),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Colors.black.withOpacity(0.2),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: isActive ? Colors.green.withOpacity(0.3) : Colors.white.withOpacity(0.1)),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Icon(isActive ? Icons.lock : Icons.lock_open, color: isActive ? Colors.greenAccent : Colors.amber, size: 16),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(cd.domain, style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.bold)),
                          ),
                          if (isPending)
                            Container(
                              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                              decoration: BoxDecoration(color: Colors.amber.withOpacity(0.2), borderRadius: BorderRadius.circular(4)),
                              child: const Text('Pending DNS', style: TextStyle(color: Colors.amber, fontSize: 10)),
                            )
                          else if (isActive)
                            Container(
                              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                              decoration: BoxDecoration(color: Colors.greenAccent.withOpacity(0.2), borderRadius: BorderRadius.circular(4)),
                              child: const Text('Active', style: TextStyle(color: Colors.greenAccent, fontSize: 10)),
                            ),
                          const SizedBox(width: 8),
                          IconButton(
                            icon: const Icon(Icons.delete_outline, color: Colors.redAccent, size: 18),
                            padding: EdgeInsets.zero,
                            constraints: const BoxConstraints(),
                            onPressed: () => _removeDomain(cd.id),
                          ),
                        ],
                      ),
                      if (isPending && cd.dnsInstructions != null) ...[
                        const SizedBox(height: 12),
                        Container(
                          padding: const EdgeInsets.all(8),
                          decoration: BoxDecoration(color: Colors.black.withOpacity(0.4), borderRadius: BorderRadius.circular(6)),
                          child: Text(cd.dnsInstructions!, style: const TextStyle(color: Colors.white70, fontSize: 11, fontFamily: 'monospace')),
                        ),
                        const SizedBox(height: 12),
                        SizedBox(
                          width: double.infinity,
                          child: ElevatedButton(
                            onPressed: _verifyingDomain ? null : () => _verifyDomain(cd.id),
                            style: ElevatedButton.styleFrom(
                              backgroundColor: Colors.amber.withOpacity(0.2),
                              foregroundColor: Colors.amber,
                              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
                            ),
                            child: _verifyingDomain
                                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.amber))
                                : const Text('Verify DNS Now'),
                          ),
                        ),
                      ],
                      if (cd.errorMessage != null && cd.errorMessage!.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        Text(cd.errorMessage!, style: const TextStyle(color: Colors.redAccent, fontSize: 11)),
                      ]
                    ],
                  ),
                );
              }),
            if (_customDomains.isEmpty)
              const Text('No custom domains attached.', style: TextStyle(color: Colors.white30, fontSize: 12)),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _domainCtrl,
                    style: const TextStyle(color: Colors.white),
                    decoration: InputDecoration(
                      hintText: 'e.g. app.example.com',
                      hintStyle: const TextStyle(color: Colors.white30),
                      filled: true,
                      fillColor: Colors.black.withOpacity(0.2),
                      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 0),
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: Colors.white.withOpacity(0.1))),
                      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide(color: Colors.white.withOpacity(0.1))),
                      focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: const BorderSide(color: Color(0xFF06B6D4))),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                ElevatedButton(
                  onPressed: _attachingDomain ? null : _attachDomain,
                  style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF06B6D4)),
                  child: _attachingDomain
                      ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.black))
                      : const Text('Attach', style: TextStyle(color: Colors.black)),
                ),
              ],
            )
          ],
        ),
      ),
    );
  }
}
