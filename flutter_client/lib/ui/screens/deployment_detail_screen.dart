import 'dart:async';
import 'package:flutter/material.dart';
import '../../api/erex_api.dart';
import '../../models/deployment_models.dart';
import '../../state/app_state.dart';
import '../widgets/glass_card.dart';

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
    } catch (e) {
      if (mounted) {
        setState(() => _isLoading = false);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error loading deployment: $e')));
      }
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
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Text('Container Logs', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
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

  Widget _buildDomains() {
    return GlassCard(
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Custom Domains', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
            const SizedBox(height: 16),
            if (_deployment!.customDomains.isNotEmpty)
              ..._deployment!.customDomains.map((cd) {
                return Padding(
                  padding: const EdgeInsets.only(bottom: 8.0),
                  child: Row(
                    children: [
                      Icon(Icons.link, color: cd.status == 'active' ? Colors.greenAccent : Colors.amber, size: 16),
                      const SizedBox(width: 8),
                      Text(cd.domain, style: const TextStyle(color: Colors.white70, fontSize: 14)),
                    ],
                  ),
                );
              }),
            if (_deployment!.customDomains.isEmpty)
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
