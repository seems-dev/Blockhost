import 'dart:async';

import 'package:flutter/foundation.dart';

import '../api/blockhost_api.dart';
import '../models/backup_models.dart';

/// Holds all backup-related state for a single server.
/// Intended to be created per BackupScreen session.
class BackupState extends ChangeNotifier {
  BackupState({required this.api, required this.serverId});

  final BlockHostApi api;
  final String serverId;

  // ── Data ──────────────────────────────────────────────────────────────────
  List<Backup> backups = [];
  BackupSchedule? schedule;
  BackupJob? activeBackupJob;
  RestoreJob? activeRestoreJob;

  // ── Loading / error ───────────────────────────────────────────────────────
  bool loading = false;
  String? error;

  // ── Polling ───────────────────────────────────────────────────────────────
  Timer? _pollTimer;
  static const _pollInterval = Duration(seconds: 4);

  // ─────────────────────────────────────────────────────────────────────────
  // Public API
  // ─────────────────────────────────────────────────────────────────────────

  /// Full refresh: backups list + schedule. Called on first open and after
  /// create/delete actions.
  Future<void> refresh() async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      final results = await Future.wait([
        api.listBackups(serverId),
        api.getBackupSchedule(serverId),
      ]);
      backups = results[0] as List<Backup>;
      schedule = results[1] as BackupSchedule?;
    } on ApiException catch (e) {
      error = e.message;
    } catch (e) {
      error = e.toString();
    } finally {
      loading = false;
      notifyListeners();
    }
  }

  // ── Create Backup ─────────────────────────────────────────────────────────

  Future<BackupJob?> createBackup({String? name, String? description}) async {
    error = null;
    notifyListeners();
    try {
      final job = await api.createBackup(
        serverId,
        name: name,
        description: description,
      );
      activeBackupJob = job;
      notifyListeners();
      if (job.isActive) _startPollingBackupJob(job.jobId);
      return job;
    } on ApiException catch (e) {
      error = e.message;
      notifyListeners();
      return null;
    } catch (e) {
      error = e.toString();
      notifyListeners();
      return null;
    }
  }

  // ── Delete Backup ─────────────────────────────────────────────────────────

  Future<bool> deleteBackup(String backupId,
      {bool deleteEvenIfPinned = false}) async {
    error = null;
    notifyListeners();
    try {
      await api.deleteBackup(serverId, backupId,
          deleteEvenIfPinned: deleteEvenIfPinned);
      await refresh();
      return true;
    } on ApiException catch (e) {
      error = e.message;
      notifyListeners();
      return false;
    } catch (e) {
      error = e.toString();
      notifyListeners();
      return false;
    }
  }

  // ── Restore ───────────────────────────────────────────────────────────────

  Future<RestoreJob?> createRestore(String backupId) async {
    error = null;
    notifyListeners();
    try {
      final job = await api.createRestore(
        serverId,
        backupId: backupId,
        restartAfterRestore: true,
        rollbackEnabled: true,
      );
      activeRestoreJob = job;
      notifyListeners();
      if (job.isActive) _startPollingRestoreJob(job.restoreJobId);
      return job;
    } on ApiException catch (e) {
      error = e.message;
      notifyListeners();
      return null;
    } catch (e) {
      error = e.toString();
      notifyListeners();
      return null;
    }
  }

  /// Clears the active restore job so the progress screen can dismiss.
  void clearRestoreJob() {
    activeRestoreJob = null;
    _pollTimer?.cancel();
    _pollTimer = null;
    notifyListeners();
  }

  /// Clears the active backup job.
  void clearBackupJob() {
    activeBackupJob = null;
    _pollTimer?.cancel();
    _pollTimer = null;
    notifyListeners();
  }

  // ── Schedule ──────────────────────────────────────────────────────────────

  Future<bool> upsertSchedule({
    required bool enabled,
    required int intervalMinutes,
    required int retentionCount,
    int? retentionDays,
    bool skipIfServerOffline = false,
    bool deferIfJobActive = true,
  }) async {
    error = null;
    notifyListeners();
    try {
      schedule = await api.upsertBackupSchedule(
        serverId,
        enabled: enabled,
        intervalMinutes: intervalMinutes,
        retentionCount: retentionCount,
        retentionDays: retentionDays,
        skipIfServerOffline: skipIfServerOffline,
        deferIfJobActive: deferIfJobActive,
      );
      notifyListeners();
      return true;
    } on ApiException catch (e) {
      error = e.message;
      notifyListeners();
      return false;
    } catch (e) {
      error = e.toString();
      notifyListeners();
      return false;
    }
  }

  Future<bool> deleteSchedule() async {
    error = null;
    notifyListeners();
    try {
      await api.deleteBackupSchedule(serverId);
      schedule = null;
      notifyListeners();
      return true;
    } on ApiException catch (e) {
      error = e.message;
      notifyListeners();
      return false;
    } catch (e) {
      error = e.toString();
      notifyListeners();
      return false;
    }
  }

  // ── Polling helpers ───────────────────────────────────────────────────────

  void _startPollingBackupJob(String jobId) {
    _pollTimer?.cancel();
    _pollTimer = Timer.periodic(_pollInterval, (_) async {
      try {
        final updated = await api.getBackupJob(serverId, jobId);
        activeBackupJob = updated;
        notifyListeners();
        if (!updated.isActive) {
          _pollTimer?.cancel();
          _pollTimer = null;
          // Refresh the full list once job finishes
          await refresh();
        }
      } catch (_) {}
    });
  }

  void _startPollingRestoreJob(String jobId) {
    _pollTimer?.cancel();
    _pollTimer = Timer.periodic(_pollInterval, (_) async {
      try {
        final updated = await api.getRestoreJob(serverId, jobId);
        activeRestoreJob = updated;
        notifyListeners();
        if (!updated.isActive) {
          _pollTimer?.cancel();
          _pollTimer = null;
        }
      } catch (_) {}
    });
  }

  /// Resume polling for an in-progress job after hot-restart / screen re-entry.
  void resumePollingIfNeeded() {
    final bj = activeBackupJob;
    if (bj != null && bj.isActive && _pollTimer == null) {
      _startPollingBackupJob(bj.jobId);
    }
    final rj = activeRestoreJob;
    if (rj != null && rj.isActive && _pollTimer == null) {
      _startPollingRestoreJob(rj.restoreJobId);
    }
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }
}
