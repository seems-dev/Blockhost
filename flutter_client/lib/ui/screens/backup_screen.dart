import 'dart:async';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../models/backup_models.dart';
import '../../state/app_state.dart';
import '../../state/backup_state.dart';

// ─── Design tokens (mirrors dashboard_screen.dart) ────────────────────────────
const _bg = Color(0xFF0A0A0A);
const _surface = Color(0xFF111111);
const _surfaceAlt = Color(0xFF141414);
const _border = Color(0xFF1E1E1E);
const _borderBright = Color(0xFF2A2A2A);
const _green = Color(0xFF00FF6A);
const _greenDim = Color(0xFF00AA44);
const _text = Color(0xFFEEEEEE);
const _muted = Color(0xFF555555);
const _mutedBright = Color(0xFF888888);
const _mono = 'monospace';
const _amber = Color(0xFFFFAA00);
const _red = Color(0xFFFF4444);
const _blue = Color(0xFF4488FF);

// ─────────────────────────────────────────────────────────────────────────────
// BackupScreen
// ─────────────────────────────────────────────────────────────────────────────

class BackupScreen extends StatefulWidget {
  const BackupScreen({
    super.key,
    required this.state,
    required this.serverId,
    required this.worldName,
  });

  final AppState state;
  final String serverId;
  final String worldName;

  @override
  State<BackupScreen> createState() => _BackupScreenState();
}

class _BackupScreenState extends State<BackupScreen> {
  late final BackupState _bs;

  @override
  void initState() {
    super.initState();
    _bs = BackupState(api: widget.state.api, serverId: widget.serverId);
    _bs.addListener(_onStateChange);
    _bs.refresh();
  }

  @override
  void dispose() {
    _bs.removeListener(_onStateChange);
    _bs.dispose();
    super.dispose();
  }

  void _onStateChange() => mounted ? setState(() {}) : null;

  // ── Create Backup dialog ──────────────────────────────────────────────────

  Future<void> _showCreateBackupDialog() async {
    final nameCtrl = TextEditingController();
    final descCtrl = TextEditingController();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => _CreateBackupDialog(
        nameCtrl: nameCtrl,
        descCtrl: descCtrl,
      ),
    );
    nameCtrl.dispose();
    descCtrl.dispose();
    if (confirmed != true || !mounted) return;

    final job = await _bs.createBackup(
      name: nameCtrl.text.trim().isEmpty ? null : nameCtrl.text.trim(),
      description: descCtrl.text.trim().isEmpty ? null : descCtrl.text.trim(),
    );

    if (!mounted) return;
    if (job != null) {
      _showSnack('Backup started!', isError: false);
    } else if (_bs.error != null) {
      _showSnack(_bs.error!, isError: true);
    }
  }

  // ── Restore flow ──────────────────────────────────────────────────────────

  Future<void> _confirmAndRestore(Backup backup) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => _RestoreConfirmDialog(backup: backup),
    );
    if (confirmed != true || !mounted) return;

    final job = await _bs.createRestore(backup.backupId);
    if (!mounted) return;
    if (job != null) {
      await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => _RestoreProgressScreen(
          bs: _bs,
          worldName: widget.worldName,
        ),
      ));
      _bs.clearRestoreJob();
      await _bs.refresh();
    } else if (_bs.error != null) {
      _showSnack(_bs.error!, isError: true);
    }
  }

  // ── Delete ────────────────────────────────────────────────────────────────

  Future<void> _confirmAndDelete(Backup backup) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => _DeleteConfirmDialog(backup: backup),
    );
    if (confirmed != true || !mounted) return;

    final ok = await _bs.deleteBackup(backup.backupId,
        deleteEvenIfPinned: backup.pinned);
    if (!mounted) return;
    if (ok) {
      _showSnack('Backup deleted.', isError: false);
    } else if (_bs.error != null) {
      _showSnack(_bs.error!, isError: true);
    }
  }

  // ── Download ──────────────────────────────────────────────────────────────

  Future<void> _downloadBackup(Backup backup) async {
    if (backup.status != BackupStatus.completed) {
      _showSnack('Backup is not completed yet.', isError: true);
      return;
    }
    // Build authenticated download URL; open via browser / download manager.
    final rawUrl = widget.state.api.backupDownloadUrl(
      widget.serverId,
      backup.backupId,
    );
    // Append token as query param because we can't inject headers in url_launcher.
    final token = widget.state.accessToken;
    final uri = Uri.parse(rawUrl).replace(
      queryParameters: {
        ...Uri.parse(rawUrl).queryParameters,
        if (token != null && token.isNotEmpty) 'token': token,
      },
    );
    final launched = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!launched && mounted) {
      _showSnack('Cannot open download link.', isError: true);
    }
  }

  // ── Schedule sheet ────────────────────────────────────────────────────────

  Future<void> _showScheduleSheet() async {
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: _surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(12)),
      ),
      builder: (ctx) => _ScheduleSheet(bs: _bs),
    );
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  void _showSnack(String msg, {required bool isError}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      backgroundColor: isError ? _red.withOpacity(.85) : _greenDim,
      content: Text(
        msg,
        style: const TextStyle(color: _text, fontFamily: _mono, fontSize: 12),
      ),
      duration: const Duration(seconds: 3),
    ));
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Build
  // ─────────────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final activeJob = _bs.activeBackupJob;
    final activeRestore = _bs.activeRestoreJob;

    return Scaffold(
      backgroundColor: _bg,
      body: SafeArea(
        child: Column(
          children: [
            // ── Top bar ─────────────────────────────────────────────────────
            _TopBar(worldName: widget.worldName),

            // ── Error banner ─────────────────────────────────────────────────
            if (_bs.error != null && !_bs.loading)
              _ErrorBanner(
                message: _bs.error!,
                onDismiss: () {
                  _bs.error = null;
                  setState(() {});
                },
              ),

            // ── Active backup job banner ─────────────────────────────────────
            if (activeJob != null && activeJob.isActive)
              _JobProgressBanner(
                label: 'BACKUP IN PROGRESS',
                phase: activeJob.phase,
                percent: activeJob.progressPercent,
                color: _blue,
              ),

            // ── Active restore job banner ────────────────────────────────────
            if (activeRestore != null && activeRestore.isActive)
              _JobProgressBanner(
                label: 'RESTORE IN PROGRESS',
                phase: activeRestore.phase,
                percent: activeRestore.progressPercent,
                color: _amber,
                onTap: () async {
                  await Navigator.of(context).push(MaterialPageRoute<void>(
                    builder: (_) => _RestoreProgressScreen(
                      bs: _bs,
                      worldName: widget.worldName,
                    ),
                  ));
                },
              ),

            Expanded(
              child: RefreshIndicator(
                color: _green,
                backgroundColor: _surface,
                onRefresh: _bs.refresh,
                child: _bs.loading && _bs.backups.isEmpty
                    ? const Center(
                        child: CircularProgressIndicator(
                            color: _green, strokeWidth: 1.5))
                    : ListView(
                        padding: const EdgeInsets.fromLTRB(20, 16, 20, 100),
                        children: [
                          // ── Create Backup ───────────────────────────────
                          _PrimaryButton(
                            icon: Icons.add_circle_outline_rounded,
                            label: 'Create Backup',
                            onTap: (_bs.activeBackupJob?.isActive == true)
                                ? null
                                : _showCreateBackupDialog,
                          ),
                          const SizedBox(height: 20),

                          // ── Auto Backup section ─────────────────────────
                          _SectionHeader(
                            title: 'Automatic Backup',
                            trailing: IconButton(
                              icon: const Icon(Icons.settings_outlined,
                                  color: _mutedBright, size: 18),
                              onPressed: _showScheduleSheet,
                              padding: EdgeInsets.zero,
                              constraints: const BoxConstraints(),
                            ),
                          ),
                          const SizedBox(height: 8),
                          _ScheduleSummaryCard(
                            schedule: _bs.schedule,
                            onConfigure: _showScheduleSheet,
                          ),
                          const SizedBox(height: 20),

                          // ── Backup History ──────────────────────────────
                          _SectionHeader(
                            title: 'Backup History',
                            trailing: _bs.loading
                                ? const SizedBox(
                                    width: 14,
                                    height: 14,
                                    child: CircularProgressIndicator(
                                        color: _green, strokeWidth: 1.5))
                                : null,
                          ),
                          const SizedBox(height: 8),

                          if (_bs.backups.isEmpty && !_bs.loading)
                            _EmptyBackupsPlaceholder(
                                onCreateBackup: _showCreateBackupDialog)
                          else
                            ..._bs.backups.map((b) => Padding(
                                  padding: const EdgeInsets.only(bottom: 10),
                                  child: _BackupCard(
                                    backup: b,
                                    onRestore:
                                        b.status == BackupStatus.completed
                                            ? () => _confirmAndRestore(b)
                                            : null,
                                    onDelete: (b.status !=
                                                BackupStatus.pending &&
                                            b.status != BackupStatus.running)
                                        ? () => _confirmAndDelete(b)
                                        : null,
                                    onDownload:
                                        b.status == BackupStatus.completed
                                            ? () => _downloadBackup(b)
                                            : null,
                                  ),
                                )),
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

// ─────────────────────────────────────────────────────────────────────────────
// Top Bar
// ─────────────────────────────────────────────────────────────────────────────

class _TopBar extends StatelessWidget {
  const _TopBar({required this.worldName});
  final String worldName;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
        decoration: const BoxDecoration(
            border: Border(bottom: BorderSide(color: _border))),
        child: Row(
          children: [
            GestureDetector(
              onTap: () => Navigator.of(context).pop(),
              child: const Row(
                children: [
                  Icon(Icons.arrow_back_rounded, color: _mutedBright, size: 18),
                  SizedBox(width: 6),
                  Text('BACK',
                      style: TextStyle(
                          color: _muted, fontSize: 11, fontFamily: _mono)),
                ],
              ),
            ),
            const Spacer(),
            Column(
              children: [
                const Text('BACKUPS',
                    style: TextStyle(
                        color: _text,
                        fontSize: 13,
                        fontWeight: FontWeight.bold,
                        fontFamily: _mono,
                        letterSpacing: 1.5)),
                Text(worldName.toUpperCase(),
                    style: const TextStyle(
                        color: _muted, fontSize: 10, fontFamily: _mono)),
              ],
            ),
            const Spacer(),
            const Icon(Icons.cloud_queue_rounded, color: _green, size: 20),
          ],
        ),
      );
}

// ─────────────────────────────────────────────────────────────────────────────
// Primary Button
// ─────────────────────────────────────────────────────────────────────────────

class _PrimaryButton extends StatelessWidget {
  const _PrimaryButton(
      {required this.icon, required this.label, required this.onTap});
  final IconData icon;
  final String label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          height: 52,
          decoration: BoxDecoration(
            color: onTap != null ? _green.withOpacity(.12) : _surface,
            border: Border.all(
                color: onTap != null ? _greenDim : _border, width: 1.5),
            borderRadius: BorderRadius.circular(4),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(icon, color: onTap != null ? _green : _muted, size: 18),
              const SizedBox(width: 10),
              Text(label,
                  style: TextStyle(
                      color: onTap != null ? _green : _muted,
                      fontWeight: FontWeight.bold,
                      fontSize: 13,
                      fontFamily: _mono)),
            ],
          ),
        ),
      );
}

// ─────────────────────────────────────────────────────────────────────────────
// Section Header
// ─────────────────────────────────────────────────────────────────────────────

class _SectionHeader extends StatelessWidget {
  const _SectionHeader({required this.title, this.trailing});
  final String title;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => Row(
        children: [
          Text(title.toUpperCase(),
              style: const TextStyle(
                  color: _mutedBright,
                  fontSize: 10,
                  fontFamily: _mono,
                  letterSpacing: 1.2)),
          const Spacer(),
          if (trailing != null) trailing!,
        ],
      );
}

// ─────────────────────────────────────────────────────────────────────────────
// Error Banner
// ─────────────────────────────────────────────────────────────────────────────

class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({required this.message, required this.onDismiss});
  final String message;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.fromLTRB(20, 10, 20, 0),
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: _red.withOpacity(.08),
          border: Border.all(color: _red.withOpacity(.3)),
          borderRadius: BorderRadius.circular(4),
        ),
        child: Row(
          children: [
            const Icon(Icons.warning_amber_rounded, color: _red, size: 14),
            const SizedBox(width: 8),
            Expanded(
                child: Text(message,
                    style: const TextStyle(
                        color: _red, fontSize: 11, fontFamily: _mono))),
            GestureDetector(
              onTap: onDismiss,
              child: const Icon(Icons.close_rounded, color: _red, size: 14),
            ),
          ],
        ),
      );
}

// ─────────────────────────────────────────────────────────────────────────────
// Job Progress Banner
// ─────────────────────────────────────────────────────────────────────────────

class _JobProgressBanner extends StatelessWidget {
  const _JobProgressBanner({
    required this.label,
    required this.phase,
    required this.percent,
    required this.color,
    this.onTap,
  });

  final String label;
  final String phase;
  final int percent;
  final Color color;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: Container(
          margin: const EdgeInsets.fromLTRB(20, 10, 20, 0),
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: color.withOpacity(.06),
            border: Border.all(color: color.withOpacity(.3)),
            borderRadius: BorderRadius.circular(4),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  _PulsingDot(color: color),
                  const SizedBox(width: 8),
                  Text(label,
                      style: TextStyle(
                          color: color,
                          fontSize: 10,
                          fontFamily: _mono,
                          fontWeight: FontWeight.bold,
                          letterSpacing: 1)),
                  const Spacer(),
                  Text('$percent%',
                      style: TextStyle(
                          color: color,
                          fontSize: 12,
                          fontFamily: _mono,
                          fontWeight: FontWeight.bold)),
                  if (onTap != null) ...[
                    const SizedBox(width: 8),
                    Icon(Icons.chevron_right_rounded, color: color, size: 16),
                  ],
                ],
              ),
              const SizedBox(height: 6),
              ClipRRect(
                borderRadius: BorderRadius.circular(2),
                child: LinearProgressIndicator(
                  value: percent / 100.0,
                  backgroundColor: color.withOpacity(.15),
                  valueColor: AlwaysStoppedAnimation<Color>(color),
                  minHeight: 2,
                ),
              ),
              const SizedBox(height: 4),
              Text(_phaseLabel(phase),
                  style: TextStyle(
                      color: color.withOpacity(.7),
                      fontSize: 10,
                      fontFamily: _mono)),
            ],
          ),
        ),
      );

  String _phaseLabel(String phase) {
    final labels = <String, String>{
      'pending': 'Waiting to start…',
      'running': 'Running…',
      'copying': 'Copying world files…',
      'compressing': 'Compressing archive…',
      'verifying': 'Verifying integrity…',
      'completed': 'Completed',
      'failed': 'Failed',
      'stopping_server': 'Stopping server…',
      'stopping': 'Stopping…',
      'extracting': 'Extracting archive…',
      'applying': 'Applying world data…',
      'restarting': 'Restarting server…',
      'rolled_back': 'Rolled back',
    };
    return labels[phase] ?? phase;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Schedule Summary Card
// ─────────────────────────────────────────────────────────────────────────────

class _ScheduleSummaryCard extends StatelessWidget {
  const _ScheduleSummaryCard(
      {required this.schedule, required this.onConfigure});
  final BackupSchedule? schedule;
  final VoidCallback onConfigure;

  @override
  Widget build(BuildContext context) {
    if (schedule == null) {
      return GestureDetector(
        onTap: onConfigure,
        child: Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: _surface,
            border: Border.all(color: _border),
            borderRadius: BorderRadius.circular(4),
          ),
          child: Row(
            children: [
              const Icon(Icons.schedule_rounded, color: _muted, size: 16),
              const SizedBox(width: 10),
              const Expanded(
                  child: Text('No automatic backups configured.',
                      style: TextStyle(
                          color: _muted, fontSize: 12, fontFamily: _mono))),
              const Icon(Icons.chevron_right_rounded, color: _muted, size: 16),
            ],
          ),
        ),
      );
    }

    final s = schedule!;
    return GestureDetector(
      onTap: onConfigure,
      child: Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: _surface,
          border: Border.all(
              color: s.enabled ? _greenDim : _border,
              width: s.enabled ? 1.5 : 1),
          borderRadius: BorderRadius.circular(4),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 7,
                  height: 7,
                  decoration: BoxDecoration(
                    color: s.enabled ? _green : _muted,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  s.enabled ? 'AUTO BACKUP ENABLED' : 'AUTO BACKUP DISABLED',
                  style: TextStyle(
                      color: s.enabled ? _green : _muted,
                      fontSize: 10,
                      fontFamily: _mono,
                      fontWeight: FontWeight.bold,
                      letterSpacing: 0.8),
                ),
                const Spacer(),
                const Icon(Icons.edit_outlined, color: _muted, size: 14),
              ],
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                _ScheduleStat(label: 'INTERVAL', value: s.intervalLabel),
                const SizedBox(width: 20),
                _ScheduleStat(
                    label: 'KEEP', value: '${s.retentionCount} backups'),
                if (s.nextRunAt != null) ...[
                  const SizedBox(width: 20),
                  _ScheduleStat(
                      label: 'NEXT', value: _formatDateTime(s.nextRunAt!)),
                ],
              ],
            ),
            if (s.lastSuccessAt != null) ...[
              const SizedBox(height: 6),
              Text('Last success: ${_formatDateTime(s.lastSuccessAt!)}',
                  style: const TextStyle(
                      color: _muted, fontSize: 10, fontFamily: _mono)),
            ],
            if (s.consecutiveFailures > 0) ...[
              const SizedBox(height: 4),
              Text('⚠ ${s.consecutiveFailures} consecutive failure(s)',
                  style: const TextStyle(
                      color: _amber, fontSize: 10, fontFamily: _mono)),
            ],
          ],
        ),
      ),
    );
  }

  static String _formatDateTime(DateTime dt) {
    final now = DateTime.now();
    final diff = dt.difference(now);
    if (diff.inMinutes.abs() < 1) return 'now';
    if (diff.inHours.abs() < 1) return '${diff.inMinutes.abs()}m';
    if (diff.inDays.abs() < 1) return '${diff.inHours.abs()}h';
    return '${dt.day}/${dt.month} ${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
  }
}

class _ScheduleStat extends StatelessWidget {
  const _ScheduleStat({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: const TextStyle(
                  color: _muted,
                  fontSize: 8,
                  fontFamily: _mono,
                  letterSpacing: 0.8)),
          const SizedBox(height: 2),
          Text(value,
              style: const TextStyle(
                  color: _text,
                  fontSize: 12,
                  fontFamily: _mono,
                  fontWeight: FontWeight.bold)),
        ],
      );
}

// ─────────────────────────────────────────────────────────────────────────────
// Empty Placeholder
// ─────────────────────────────────────────────────────────────────────────────

class _EmptyBackupsPlaceholder extends StatelessWidget {
  const _EmptyBackupsPlaceholder({required this.onCreateBackup});
  final VoidCallback onCreateBackup;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 40),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.cloud_off_rounded, color: _muted, size: 40),
              const SizedBox(height: 12),
              const Text('No backups yet.',
                  style: TextStyle(
                      color: _muted, fontSize: 13, fontFamily: _mono)),
              const SizedBox(height: 4),
              const Text('Create your first backup to protect your world.',
                  style:
                      TextStyle(color: _muted, fontSize: 11, fontFamily: _mono),
                  textAlign: TextAlign.center),
              const SizedBox(height: 16),
              GestureDetector(
                onTap: onCreateBackup,
                child: Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
                  decoration: BoxDecoration(
                    border: Border.all(color: _greenDim),
                    borderRadius: BorderRadius.circular(3),
                  ),
                  child: const Text('Create Backup',
                      style: TextStyle(
                          color: _green,
                          fontFamily: _mono,
                          fontWeight: FontWeight.bold)),
                ),
              ),
            ],
          ),
        ),
      );
}

// ─────────────────────────────────────────────────────────────────────────────
// Backup Card
// ─────────────────────────────────────────────────────────────────────────────

class _BackupCard extends StatelessWidget {
  const _BackupCard({
    required this.backup,
    required this.onRestore,
    required this.onDelete,
    required this.onDownload,
  });

  final Backup backup;
  final VoidCallback? onRestore;
  final VoidCallback? onDelete;
  final VoidCallback? onDownload;

  Color get _statusColor {
    switch (backup.status) {
      case BackupStatus.completed:
        return _green;
      case BackupStatus.failed:
        return _red;
      case BackupStatus.pending:
      case BackupStatus.running:
      case BackupStatus.copying:
      case BackupStatus.compressing:
      case BackupStatus.verifying:
        return _blue;
      default:
        return _muted;
    }
  }

  String get _statusLabel {
    switch (backup.status) {
      case BackupStatus.completed:
        return 'COMPLETE';
      case BackupStatus.failed:
        return 'FAILED';
      case BackupStatus.pending:
        return 'PENDING';
      case BackupStatus.running:
        return 'RUNNING';
      case BackupStatus.copying:
        return 'COPYING';
      case BackupStatus.compressing:
        return 'COMPRESSING';
      case BackupStatus.verifying:
        return 'VERIFYING';
      case BackupStatus.deleted:
        return 'DELETED';
    }
  }

  @override
  Widget build(BuildContext context) {
    final statusColor = _statusColor;
    return Container(
      decoration: BoxDecoration(
        color: _surface,
        border: Border.all(
          color: backup.status == BackupStatus.completed
              ? _border
              : statusColor.withOpacity(.3),
        ),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Column(
        children: [
          // Header
          Padding(
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 10),
            child: Row(
              children: [
                // Kind icon
                Container(
                  width: 32,
                  height: 32,
                  decoration: BoxDecoration(
                    color: statusColor.withOpacity(.08),
                    borderRadius: BorderRadius.circular(3),
                    border: Border.all(color: statusColor.withOpacity(.2)),
                  ),
                  child: Icon(
                    backup.kind == BackupKind.scheduled
                        ? Icons.schedule_rounded
                        : Icons.person_outline_rounded,
                    color: statusColor,
                    size: 16,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Flexible(
                            child: Text(
                              backup.displayName,
                              style: const TextStyle(
                                color: _text,
                                fontSize: 13,
                                fontWeight: FontWeight.bold,
                                fontFamily: _mono,
                              ),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          if (backup.pinned) ...[
                            const SizedBox(width: 6),
                            const Icon(Icons.push_pin_rounded,
                                color: _amber, size: 12),
                          ],
                        ],
                      ),
                      const SizedBox(height: 2),
                      Text(
                        _formatDate(backup.createdAt),
                        style: const TextStyle(
                            color: _muted, fontSize: 10, fontFamily: _mono),
                      ),
                    ],
                  ),
                ),
                // Status badge
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: statusColor.withOpacity(.1),
                    border: Border.all(color: statusColor.withOpacity(.35)),
                    borderRadius: BorderRadius.circular(2),
                  ),
                  child: Text(_statusLabel,
                      style: TextStyle(
                          color: statusColor,
                          fontSize: 9,
                          fontFamily: _mono,
                          fontWeight: FontWeight.bold,
                          letterSpacing: 0.5)),
                ),
              ],
            ),
          ),

          // Stats row
          Container(
            margin: const EdgeInsets.fromLTRB(14, 0, 14, 10),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            decoration: BoxDecoration(
              color: _bg,
              border: Border.all(color: _border),
              borderRadius: BorderRadius.circular(3),
            ),
            child: Row(
              children: [
                _StatChip(label: 'SIZE', value: backup.displaySize),
                const SizedBox(width: 16),
                _StatChip(label: 'TYPE', value: backup.kind.name.toUpperCase()),
                if (backup.failureMessage != null) ...[
                  const SizedBox(width: 16),
                  Flexible(
                    child: Text(backup.failureMessage!,
                        style: const TextStyle(
                            color: _red, fontSize: 10, fontFamily: _mono),
                        overflow: TextOverflow.ellipsis),
                  ),
                ],
              ],
            ),
          ),

          // Action bar (only for completed)
          if (backup.status == BackupStatus.completed)
            Container(
              decoration: const BoxDecoration(
                  border: Border(top: BorderSide(color: _border))),
              child: Row(
                children: [
                  if (onRestore != null)
                    Expanded(
                      child: _CardActionBtn(
                        icon: Icons.restore_rounded,
                        label: 'Restore',
                        onTap: onRestore,
                        color: _amber,
                      ),
                    ),
                  if (onRestore != null && onDownload != null)
                    Container(width: 1, height: 40, color: _border),
                  if (onDownload != null)
                    Expanded(
                      child: _CardActionBtn(
                        icon: Icons.download_rounded,
                        label: 'Download',
                        onTap: onDownload,
                        color: _blue,
                      ),
                    ),
                  if (onDelete != null) ...[
                    Container(width: 1, height: 40, color: _border),
                    SizedBox(
                      width: 50,
                      height: 40,
                      child: IconButton(
                        onPressed: onDelete,
                        icon: const Icon(Icons.delete_outline_rounded,
                            color: _red, size: 17),
                        padding: EdgeInsets.zero,
                      ),
                    ),
                  ],
                ],
              ),
            )
          else if (onDelete != null &&
              backup.status != BackupStatus.pending &&
              backup.status != BackupStatus.running)
            Container(
              decoration: const BoxDecoration(
                  border: Border(top: BorderSide(color: _border))),
              child: SizedBox(
                height: 40,
                child: _CardActionBtn(
                  icon: Icons.delete_outline_rounded,
                  label: 'Delete',
                  onTap: onDelete,
                  color: _red,
                ),
              ),
            ),
        ],
      ),
    );
  }

  static String _formatDate(DateTime dt) {
    final now = DateTime.now();
    final diff = now.difference(dt);
    if (diff.inSeconds < 60) return 'just now';
    if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
    if (diff.inHours < 24) return '${diff.inHours}h ago';
    return '${dt.day}/${dt.month}/${dt.year} ${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
  }
}

class _StatChip extends StatelessWidget {
  const _StatChip({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: const TextStyle(
                  color: _muted,
                  fontSize: 8,
                  fontFamily: _mono,
                  letterSpacing: 1)),
          const SizedBox(height: 1),
          Text(value,
              style: const TextStyle(
                  color: _text,
                  fontSize: 12,
                  fontFamily: _mono,
                  fontWeight: FontWeight.bold)),
        ],
      );
}

class _CardActionBtn extends StatelessWidget {
  const _CardActionBtn(
      {required this.icon,
      required this.label,
      required this.onTap,
      required this.color});
  final IconData icon;
  final String label;
  final VoidCallback? onTap;
  final Color color;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: SizedBox(
          height: 40,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(icon, size: 13, color: onTap != null ? color : _muted),
              const SizedBox(width: 5),
              Text(label,
                  style: TextStyle(
                      color: onTap != null ? color : _muted,
                      fontSize: 11,
                      fontFamily: _mono)),
            ],
          ),
        ),
      );
}

// ─────────────────────────────────────────────────────────────────────────────
// Create Backup Dialog
// ─────────────────────────────────────────────────────────────────────────────

class _CreateBackupDialog extends StatelessWidget {
  const _CreateBackupDialog({required this.nameCtrl, required this.descCtrl});
  final TextEditingController nameCtrl;
  final TextEditingController descCtrl;

  @override
  Widget build(BuildContext context) => Dialog(
        backgroundColor: _surface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text('Create Backup',
                  style: TextStyle(
                      color: _text,
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                      fontFamily: _mono)),
              const SizedBox(height: 4),
              const Text('Both fields are optional.',
                  style: TextStyle(
                      color: _muted, fontSize: 11, fontFamily: _mono)),
              const SizedBox(height: 16),
              _DarkTextField(
                  controller: nameCtrl, label: 'Backup name (optional)'),
              const SizedBox(height: 10),
              _DarkTextField(
                  controller: descCtrl,
                  label: 'Description (optional)',
                  maxLines: 3),
              const SizedBox(height: 20),
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('Cancel',
                        style:
                            TextStyle(color: _mutedBright, fontFamily: _mono)),
                  ),
                  const SizedBox(width: 8),
                  GestureDetector(
                    onTap: () => Navigator.pop(context, true),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 20, vertical: 10),
                      decoration: BoxDecoration(
                        color: _green.withOpacity(.12),
                        border: Border.all(color: _greenDim),
                        borderRadius: BorderRadius.circular(3),
                      ),
                      child: const Text('Start Backup',
                          style: TextStyle(
                              color: _green,
                              fontFamily: _mono,
                              fontWeight: FontWeight.bold)),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      );
}

// ─────────────────────────────────────────────────────────────────────────────
// Restore Confirm Dialog
// ─────────────────────────────────────────────────────────────────────────────

class _RestoreConfirmDialog extends StatelessWidget {
  const _RestoreConfirmDialog({required this.backup});
  final Backup backup;

  @override
  Widget build(BuildContext context) => Dialog(
        backgroundColor: _surface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  const Icon(Icons.warning_amber_rounded,
                      color: _amber, size: 20),
                  const SizedBox(width: 10),
                  const Text('Restore Backup',
                      style: TextStyle(
                          color: _text,
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                          fontFamily: _mono)),
                ],
              ),
              const SizedBox(height: 14),
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: _amber.withOpacity(.07),
                  border: Border.all(color: _amber.withOpacity(.3)),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: const Text(
                  'This will stop the server and replace the current world with the selected backup. This cannot be undone unless rollback is triggered automatically.',
                  style: TextStyle(
                      color: _amber,
                      fontSize: 12,
                      fontFamily: _mono,
                      height: 1.5),
                ),
              ),
              const SizedBox(height: 12),
              Text('Restoring: ${backup.displayName}',
                  style: const TextStyle(
                      color: _mutedBright, fontSize: 11, fontFamily: _mono)),
              Text('Created: ${backup.createdAt.toLocal()}',
                  style: const TextStyle(
                      color: _muted, fontSize: 10, fontFamily: _mono)),
              const SizedBox(height: 20),
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('Cancel',
                        style:
                            TextStyle(color: _mutedBright, fontFamily: _mono)),
                  ),
                  const SizedBox(width: 8),
                  GestureDetector(
                    onTap: () => Navigator.pop(context, true),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 20, vertical: 10),
                      decoration: BoxDecoration(
                        color: _amber.withOpacity(.12),
                        border: Border.all(color: _amber),
                        borderRadius: BorderRadius.circular(3),
                      ),
                      child: const Text('Yes, Restore',
                          style: TextStyle(
                              color: _amber,
                              fontFamily: _mono,
                              fontWeight: FontWeight.bold)),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      );
}

// ─────────────────────────────────────────────────────────────────────────────
// Delete Confirm Dialog
// ─────────────────────────────────────────────────────────────────────────────

class _DeleteConfirmDialog extends StatelessWidget {
  const _DeleteConfirmDialog({required this.backup});
  final Backup backup;

  @override
  Widget build(BuildContext context) => Dialog(
        backgroundColor: _surface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text('Delete Backup',
                  style: TextStyle(
                      color: _text,
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                      fontFamily: _mono)),
              const SizedBox(height: 10),
              Text(
                'Delete "${backup.displayName}"? This action cannot be undone.',
                style: const TextStyle(
                    color: _mutedBright,
                    fontSize: 12,
                    fontFamily: _mono,
                    height: 1.4),
              ),
              if (backup.pinned) ...[
                const SizedBox(height: 8),
                const Text('⚠ This backup is pinned.',
                    style: TextStyle(
                        color: _amber, fontSize: 11, fontFamily: _mono)),
              ],
              const SizedBox(height: 20),
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('Cancel',
                        style:
                            TextStyle(color: _mutedBright, fontFamily: _mono)),
                  ),
                  const SizedBox(width: 8),
                  GestureDetector(
                    onTap: () => Navigator.pop(context, true),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 20, vertical: 10),
                      decoration: BoxDecoration(
                        color: _red.withOpacity(.12),
                        border: Border.all(color: _red),
                        borderRadius: BorderRadius.circular(3),
                      ),
                      child: const Text('Delete',
                          style: TextStyle(
                              color: _red,
                              fontFamily: _mono,
                              fontWeight: FontWeight.bold)),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      );
}

// ─────────────────────────────────────────────────────────────────────────────
// Restore Progress Screen
// ─────────────────────────────────────────────────────────────────────────────

class _RestoreProgressScreen extends StatefulWidget {
  const _RestoreProgressScreen({required this.bs, required this.worldName});
  final BackupState bs;
  final String worldName;

  @override
  State<_RestoreProgressScreen> createState() => _RestoreProgressScreenState();
}

class _RestoreProgressScreenState extends State<_RestoreProgressScreen> {
  @override
  void initState() {
    super.initState();
    widget.bs.addListener(_onUpdate);
    widget.bs.resumePollingIfNeeded();
  }

  @override
  void dispose() {
    widget.bs.removeListener(_onUpdate);
    super.dispose();
  }

  void _onUpdate() {
    if (mounted) setState(() {});
  }

  String _phaseLabel(String phase) {
    const labels = <String, String>{
      'pending': 'Waiting to start…',
      'running': 'Running…',
      'stopping_server': 'Stopping server…',
      'stopping': 'Stopping server…',
      'extracting': 'Extracting backup…',
      'applying': 'Applying world data…',
      'restarting': 'Restarting server…',
      'completed': 'Restore completed!',
      'failed': 'Restore failed',
      'rolled_back': 'Rolled back to previous state',
    };
    return labels[phase] ?? phase;
  }

  @override
  Widget build(BuildContext context) {
    final job = widget.bs.activeRestoreJob;
    if (job == null) {
      return Scaffold(
        backgroundColor: _bg,
        body: const Center(
          child: Text('No restore job found.',
              style: TextStyle(color: _muted, fontFamily: _mono)),
        ),
      );
    }

    final isCompleted = job.isCompleted;
    final isFailed = job.isFailed;
    final color = isCompleted
        ? _green
        : isFailed
            ? _red
            : _amber;

    return Scaffold(
      backgroundColor: _bg,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SizedBox(height: 20),
              // Status icon
              Center(
                child: Container(
                  width: 80,
                  height: 80,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: color.withOpacity(.08),
                    border: Border.all(color: color.withOpacity(.4), width: 2),
                    boxShadow: [
                      BoxShadow(
                          color: color.withOpacity(.2),
                          blurRadius: 24,
                          spreadRadius: 2),
                    ],
                  ),
                  child: isCompleted
                      ? const Icon(Icons.check_rounded, color: _green, size: 36)
                      : isFailed
                          ? const Icon(Icons.close_rounded,
                              color: _red, size: 36)
                          : const _SpinningIcon(color: _amber),
                ),
              ),
              const SizedBox(height: 20),
              Center(
                child: Text(
                  isCompleted
                      ? 'RESTORE COMPLETE'
                      : isFailed
                          ? 'RESTORE FAILED'
                          : 'RESTORING…',
                  style: TextStyle(
                    color: color,
                    fontSize: 14,
                    fontFamily: _mono,
                    fontWeight: FontWeight.bold,
                    letterSpacing: 2,
                  ),
                ),
              ),
              const SizedBox(height: 8),
              Center(
                child: Text(
                  _phaseLabel(job.phase),
                  style: const TextStyle(
                      color: _mutedBright, fontSize: 12, fontFamily: _mono),
                ),
              ),
              const SizedBox(height: 24),

              // Progress bar
              ClipRRect(
                borderRadius: BorderRadius.circular(3),
                child: LinearProgressIndicator(
                  value: isCompleted ? 1.0 : job.progressPercent / 100.0,
                  backgroundColor: _border,
                  valueColor: AlwaysStoppedAnimation<Color>(color),
                  minHeight: 4,
                ),
              ),
              const SizedBox(height: 8),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text('${job.progressPercent}%',
                      style: TextStyle(
                          color: color,
                          fontSize: 13,
                          fontFamily: _mono,
                          fontWeight: FontWeight.bold)),
                  Text(widget.worldName.toUpperCase(),
                      style: const TextStyle(
                          color: _muted, fontSize: 10, fontFamily: _mono)),
                ],
              ),

              if (isFailed && job.errorMessage != null) ...[
                const SizedBox(height: 20),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: _red.withOpacity(.07),
                    border: Border.all(color: _red.withOpacity(.3)),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('ERROR',
                          style: TextStyle(
                              color: _red,
                              fontSize: 9,
                              fontFamily: _mono,
                              letterSpacing: 1)),
                      const SizedBox(height: 4),
                      Text(job.errorMessage!,
                          style: const TextStyle(
                              color: _mutedBright,
                              fontSize: 12,
                              fontFamily: _mono,
                              height: 1.4)),
                      if (job.rollbackEnabled) ...[
                        const SizedBox(height: 6),
                        const Text(
                          'Rollback was enabled. Your world should be intact.',
                          style: TextStyle(
                              color: _amber, fontSize: 10, fontFamily: _mono),
                        ),
                      ],
                    ],
                  ),
                ),
              ],

              const Spacer(),

              if (isCompleted || isFailed)
                GestureDetector(
                  onTap: () => Navigator.of(context).pop(),
                  child: Container(
                    height: 50,
                    decoration: BoxDecoration(
                      color: color.withOpacity(.1),
                      border: Border.all(color: color.withOpacity(.5)),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Center(
                      child: Text(
                        isCompleted ? 'Done' : 'Close',
                        style: TextStyle(
                            color: color,
                            fontFamily: _mono,
                            fontWeight: FontWeight.bold,
                            fontSize: 14),
                      ),
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

// ─────────────────────────────────────────────────────────────────────────────
// Schedule Bottom Sheet
// ─────────────────────────────────────────────────────────────────────────────

class _ScheduleSheet extends StatefulWidget {
  const _ScheduleSheet({required this.bs});
  final BackupState bs;

  @override
  State<_ScheduleSheet> createState() => _ScheduleSheetState();
}

class _ScheduleSheetState extends State<_ScheduleSheet> {
  bool _enabled = false;
  int _intervalMinutes = 360; // 6h default
  int _retentionCount = 7;
  bool _saving = false;
  String? _error;

  // Preset intervals shown in the UI
  static const _intervals = [
    _IntervalOption(label: 'Every 6 hours', minutes: 360),
    _IntervalOption(label: 'Every 12 hours', minutes: 720),
    _IntervalOption(label: 'Daily', minutes: 1440),
    _IntervalOption(label: 'Every 2 days', minutes: 2880),
    _IntervalOption(label: 'Weekly', minutes: 10080),
  ];

  @override
  void initState() {
    super.initState();
    final s = widget.bs.schedule;
    if (s != null) {
      _enabled = s.enabled;
      // Snap to nearest preset or keep as-is
      final m = s.intervalMinutes ?? 360;
      _intervalMinutes = _intervals.map((i) => i.minutes).contains(m) ? m : 360;
      _retentionCount = s.retentionCount;
    }
    widget.bs.addListener(_onBsUpdate);
  }

  @override
  void dispose() {
    widget.bs.removeListener(_onBsUpdate);
    super.dispose();
  }

  void _onBsUpdate() {
    if (mounted) setState(() {});
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    final ok = await widget.bs.upsertSchedule(
      enabled: _enabled,
      intervalMinutes: _intervalMinutes,
      retentionCount: _retentionCount,
    );
    if (!mounted) return;
    if (ok) {
      Navigator.of(context).pop();
    } else {
      setState(() {
        _saving = false;
        _error = widget.bs.error;
      });
    }
  }

  Future<void> _deleteSchedule() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => Dialog(
        backgroundColor: _surface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text('Remove Schedule?',
                  style: TextStyle(
                      color: _text,
                      fontFamily: _mono,
                      fontSize: 15,
                      fontWeight: FontWeight.bold)),
              const SizedBox(height: 10),
              const Text(
                  'This will disable automatic backups and remove the schedule.',
                  style: TextStyle(
                      color: _mutedBright, fontFamily: _mono, fontSize: 12)),
              const SizedBox(height: 20),
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  TextButton(
                    onPressed: () => Navigator.pop(ctx, false),
                    child: const Text('Cancel',
                        style: TextStyle(color: _mutedBright)),
                  ),
                  const SizedBox(width: 8),
                  TextButton(
                    onPressed: () => Navigator.pop(ctx, true),
                    child: const Text('Remove',
                        style: TextStyle(
                            color: _red, fontWeight: FontWeight.bold)),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
    if (confirmed != true || !mounted) return;
    setState(() => _saving = true);
    await widget.bs.deleteSchedule();
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding:
          EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Handle
            Center(
              child: Container(
                width: 36,
                height: 4,
                decoration: BoxDecoration(
                    color: _borderBright,
                    borderRadius: BorderRadius.circular(2)),
              ),
            ),
            const SizedBox(height: 16),
            const Text('Automatic Backup',
                style: TextStyle(
                    color: _text,
                    fontSize: 16,
                    fontWeight: FontWeight.bold,
                    fontFamily: _mono)),
            const SizedBox(height: 4),
            const Text(
                'Configure automatic backup intervals and retention policy.',
                style:
                    TextStyle(color: _muted, fontSize: 11, fontFamily: _mono)),
            const SizedBox(height: 20),

            // Enable toggle
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                color: _surfaceAlt,
                border: Border.all(
                    color: _enabled ? _greenDim : _border,
                    width: _enabled ? 1.5 : 1),
                borderRadius: BorderRadius.circular(4),
              ),
              child: Row(
                children: [
                  const Icon(Icons.schedule_rounded,
                      color: _mutedBright, size: 18),
                  const SizedBox(width: 10),
                  const Expanded(
                    child: Text('Enable Automatic Backups',
                        style: TextStyle(
                            color: _text,
                            fontSize: 13,
                            fontFamily: _mono,
                            fontWeight: FontWeight.w600)),
                  ),
                  Switch(
                    value: _enabled,
                    onChanged: (v) => setState(() => _enabled = v),
                    activeColor: _green,
                    activeTrackColor: _greenDim.withOpacity(.3),
                    inactiveThumbColor: _muted,
                    inactiveTrackColor: _border,
                  ),
                ],
              ),
            ),

            if (_enabled) ...[
              const SizedBox(height: 16),
              const Text('BACKUP INTERVAL',
                  style: TextStyle(
                      color: _muted,
                      fontSize: 9,
                      fontFamily: _mono,
                      letterSpacing: 1.2)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: _intervals
                    .map((opt) => GestureDetector(
                          onTap: () =>
                              setState(() => _intervalMinutes = opt.minutes),
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 14, vertical: 8),
                            decoration: BoxDecoration(
                              color: _intervalMinutes == opt.minutes
                                  ? _green.withOpacity(.1)
                                  : _surfaceAlt,
                              border: Border.all(
                                  color: _intervalMinutes == opt.minutes
                                      ? _greenDim
                                      : _border,
                                  width: _intervalMinutes == opt.minutes
                                      ? 1.5
                                      : 1),
                              borderRadius: BorderRadius.circular(3),
                            ),
                            child: Text(opt.label,
                                style: TextStyle(
                                    color: _intervalMinutes == opt.minutes
                                        ? _green
                                        : _mutedBright,
                                    fontSize: 11,
                                    fontFamily: _mono,
                                    fontWeight: _intervalMinutes == opt.minutes
                                        ? FontWeight.bold
                                        : FontWeight.normal)),
                          ),
                        ))
                    .toList(),
              ),
              const SizedBox(height: 16),
              const Text('RETENTION — KEEP LAST N BACKUPS',
                  style: TextStyle(
                      color: _muted,
                      fontSize: 9,
                      fontFamily: _mono,
                      letterSpacing: 1.2)),
              const SizedBox(height: 8),
              Row(
                children: [
                  GestureDetector(
                    onTap: _retentionCount > 1
                        ? () => setState(() => _retentionCount--)
                        : null,
                    child: Container(
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(
                        color: _surfaceAlt,
                        border: Border.all(color: _border),
                        borderRadius: BorderRadius.circular(3),
                      ),
                      child: const Icon(Icons.remove_rounded,
                          color: _mutedBright, size: 16),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Text('$_retentionCount',
                      style: const TextStyle(
                          color: _text,
                          fontSize: 22,
                          fontFamily: _mono,
                          fontWeight: FontWeight.bold)),
                  const SizedBox(width: 6),
                  const Text('backups',
                      style: TextStyle(
                          color: _muted, fontSize: 12, fontFamily: _mono)),
                  const SizedBox(width: 12),
                  GestureDetector(
                    onTap: _retentionCount < 50
                        ? () => setState(() => _retentionCount++)
                        : null,
                    child: Container(
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(
                        color: _surfaceAlt,
                        border: Border.all(color: _border),
                        borderRadius: BorderRadius.circular(3),
                      ),
                      child: const Icon(Icons.add_rounded,
                          color: _mutedBright, size: 16),
                    ),
                  ),
                ],
              ),
            ],

            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(_error!,
                  style: const TextStyle(
                      color: _red, fontSize: 11, fontFamily: _mono)),
            ],

            const SizedBox(height: 24),
            GestureDetector(
              onTap: _saving ? null : _save,
              child: Container(
                height: 50,
                decoration: BoxDecoration(
                  color: _saving ? _surface : _green.withOpacity(.12),
                  border: Border.all(
                      color: _saving ? _border : _greenDim, width: 1.5),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Center(
                  child: _saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                              color: _green, strokeWidth: 1.5))
                      : const Text('Save Schedule',
                          style: TextStyle(
                              color: _green,
                              fontFamily: _mono,
                              fontWeight: FontWeight.bold,
                              fontSize: 14)),
                ),
              ),
            ),

            if (widget.bs.schedule != null) ...[
              const SizedBox(height: 12),
              GestureDetector(
                onTap: _saving ? null : _deleteSchedule,
                child: Container(
                  height: 44,
                  decoration: BoxDecoration(
                    border: Border.all(color: _red.withOpacity(.4)),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: const Center(
                    child: Text('Remove Schedule',
                        style: TextStyle(
                            color: _red, fontFamily: _mono, fontSize: 13)),
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _IntervalOption {
  const _IntervalOption({required this.label, required this.minutes});
  final String label;
  final int minutes;
}

// ─────────────────────────────────────────────────────────────────────────────
// Dark TextField helper
// ─────────────────────────────────────────────────────────────────────────────

class _DarkTextField extends StatelessWidget {
  const _DarkTextField(
      {required this.controller, required this.label, this.maxLines = 1});
  final TextEditingController controller;
  final String label;
  final int maxLines;

  @override
  Widget build(BuildContext context) => TextField(
        controller: controller,
        maxLines: maxLines,
        style: const TextStyle(color: _text, fontFamily: _mono, fontSize: 13),
        cursorColor: _green,
        decoration: InputDecoration(
          labelText: label,
          labelStyle:
              const TextStyle(color: _muted, fontSize: 12, fontFamily: _mono),
          filled: true,
          fillColor: _bg,
          enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(4),
              borderSide: const BorderSide(color: _border)),
          focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(4),
              borderSide: const BorderSide(color: _greenDim, width: 1.5)),
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        ),
      );
}

// ─────────────────────────────────────────────────────────────────────────────
// Misc shared widgets
// ─────────────────────────────────────────────────────────────────────────────

class _PulsingDot extends StatefulWidget {
  const _PulsingDot({required this.color});
  final Color color;

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
          width: 7,
          height: 7,
          decoration:
              BoxDecoration(color: widget.color, shape: BoxShape.circle),
        ),
      );
}

class _SpinningIcon extends StatefulWidget {
  const _SpinningIcon({required this.color});
  final Color color;

  @override
  State<_SpinningIcon> createState() => _SpinningIconState();
}

class _SpinningIconState extends State<_SpinningIcon>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl =
        AnimationController(vsync: this, duration: const Duration(seconds: 2))
          ..repeat();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => RotationTransition(
        turns: _ctrl,
        child: Icon(Icons.sync_rounded, color: widget.color, size: 32),
      );
}
