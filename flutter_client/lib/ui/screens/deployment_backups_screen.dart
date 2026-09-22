import 'package:flutter/material.dart';
import '../../state/app_state.dart';
import '../../models/deployment_models.dart';
import '../widgets/glass_card.dart';
import 'package:intl/intl.dart';

class DeploymentBackupsScreen extends StatefulWidget {
  const DeploymentBackupsScreen({super.key, required this.state, required this.deployment});
  
  final AppState state;
  final AppDeployment deployment;

  @override
  State<DeploymentBackupsScreen> createState() => _DeploymentBackupsScreenState();
}

class _DeploymentBackupsScreenState extends State<DeploymentBackupsScreen> {
  bool _isLoading = true;
  bool _isActioning = false;
  List<Map<String, dynamic>> _snapshots = [];

  @override
  void initState() {
    super.initState();
    _loadSnapshots();
  }

  Future<void> _loadSnapshots() async {
    setState(() => _isLoading = true);
    try {
      final data = await widget.state.api.getVolumeSnapshots(widget.deployment.id);
      if (mounted) {
        setState(() {
          _snapshots = data;
        });
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error loading backups: $e')));
      }
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _takeSnapshot() async {
    setState(() => _isActioning = true);
    try {
      await widget.state.api.createVolumeSnapshot(widget.deployment.id);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Backup created successfully!'), backgroundColor: Colors.green),
        );
      }
      await _loadSnapshots();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed to take backup: $e')));
      }
    } finally {
      if (mounted) setState(() => _isActioning = false);
    }
  }

  Future<void> _restoreSnapshot(String s3Key) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF1E293B),
        title: const Text('Restore Backup?', style: TextStyle(color: Colors.white)),
        content: const Text(
          'This will overwrite your current database with the backup data. This action cannot be undone. Are you sure?',
          style: TextStyle(color: Colors.white70),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel', style: TextStyle(color: Colors.white54)),
          ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Restore', style: TextStyle(color: Colors.redAccent)),
          ),
        ],
      ),
    );

    if (confirmed != true) return;

    setState(() => _isActioning = true);
    try {
      await widget.state.api.restoreVolumeSnapshot(widget.deployment.id, s3Key);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Backup restored successfully!'), backgroundColor: Colors.green),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed to restore backup: $e')));
      }
    } finally {
      if (mounted) setState(() => _isActioning = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final bool isRunning = widget.deployment.state == 'running';

    return Scaffold(
      backgroundColor: Colors.transparent,
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [Color(0xFF0F172A), Color(0xFF1E293B)],
          ),
        ),
        child: SafeArea(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // Header
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
                child: Row(
                  children: [
                    IconButton(
                      icon: const Icon(Icons.arrow_back_ios, color: Colors.white70, size: 20),
                      onPressed: () => Navigator.of(context).pop(),
                    ),
                    const Text('Database Backups', style: TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.bold)),
                  ],
                ),
              ),

              if (isRunning)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 8.0),
                  child: Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: Colors.orange.withOpacity(0.1),
                      border: Border.all(color: Colors.orange.withOpacity(0.3)),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Row(
                      children: [
                        Icon(Icons.warning_amber_rounded, color: Colors.orange.withOpacity(0.8), size: 20),
                        const SizedBox(width: 8),
                        const Expanded(
                          child: Text(
                            'Your database is currently running. You must stop the deployment before taking or restoring a snapshot to prevent data corruption.',
                            style: TextStyle(color: Colors.orange, fontSize: 12),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),

              Padding(
                padding: const EdgeInsets.all(16.0),
                child: ElevatedButton.icon(
                  onPressed: isRunning || _isLoading || _isActioning ? null : _takeSnapshot,
                  icon: _isActioning 
                      ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2))
                      : const Icon(Icons.backup, size: 18),
                  label: const Text('Take Snapshot Now'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF06B6D4),
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                    disabledBackgroundColor: Colors.grey.withOpacity(0.3),
                  ),
                ),
              ),

              if (_isLoading)
                const Expanded(child: Center(child: CircularProgressIndicator(color: Color(0xFF06B6D4))))
              else
                Expanded(
                  child: RefreshIndicator(
                    onRefresh: _loadSnapshots,
                    child: _snapshots.isEmpty
                        ? ListView(
                            children: const [
                              Padding(
                                padding: EdgeInsets.all(32.0),
                                child: Center(
                                  child: Text('No backups found.', style: TextStyle(color: Colors.white54)),
                                ),
                              )
                            ],
                          )
                        : ListView.builder(
                            padding: const EdgeInsets.all(16),
                            itemCount: _snapshots.length,
                            itemBuilder: (context, index) {
                              final snap = _snapshots[index];
                              final ts = snap['timestamp'] as int;
                              final date = DateTime.fromMillisecondsSinceEpoch(ts * 1000);
                              final formatted = DateFormat('MMM d, yyyy • h:mm a').format(date);
                              
                              return Container(
                                margin: const EdgeInsets.only(bottom: 12),
                                child: GlassCard(
                                  child: ListTile(
                                    contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                                    leading: Container(
                                      padding: const EdgeInsets.all(8),
                                      decoration: BoxDecoration(
                                        color: const Color(0xFF06B6D4).withOpacity(0.1),
                                        shape: BoxShape.circle,
                                      ),
                                      child: const Icon(Icons.history, color: Color(0xFF06B6D4), size: 20),
                                    ),
                                    title: Text(formatted, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
                                    subtitle: Text(snap['s3_key'].split('/').last, style: const TextStyle(color: Colors.white54, fontSize: 11)),
                                    trailing: isRunning ? null : TextButton.icon(
                                      onPressed: _isActioning ? null : () => _restoreSnapshot(snap['s3_key']),
                                      icon: const Icon(Icons.restore, size: 16, color: Colors.redAccent),
                                      label: const Text('Restore', style: TextStyle(color: Colors.redAccent)),
                                      style: TextButton.styleFrom(
                                        backgroundColor: Colors.redAccent.withOpacity(0.1),
                                      ),
                                    ),
                                  ),
                                ),
                              );
                            },
                          ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
