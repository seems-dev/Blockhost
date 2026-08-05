// ignore_for_file: non_constant_identifier_names

/// Mirrors BackupStatus enum from backend.
enum BackupStatus {
  pending,
  running,
  copying,
  compressing,
  verifying,
  completed,
  failed,
  deleted,
}

/// Mirrors BackupKind enum from backend.
enum BackupKind {
  manual,
  scheduled,
}

/// Mirrors RestoreStatus enum from backend.
enum RestoreStatus {
  pending,
  running,
  stopping_server,
  stopping,
  extracting,
  applying,
  restarting,
  completed,
  failed,
  rolled_back,
}

BackupStatus _backupStatusFromString(String s) {
  return BackupStatus.values.firstWhere(
    (e) => e.name == s,
    orElse: () => BackupStatus.pending,
  );
}

BackupKind _backupKindFromString(String s) {
  return BackupKind.values.firstWhere(
    (e) => e.name == s,
    orElse: () => BackupKind.manual,
  );
}

RestoreStatus _restoreStatusFromString(String s) {
  return RestoreStatus.values.firstWhere(
    (e) => e.name == s,
    orElse: () => RestoreStatus.pending,
  );
}

DateTime? _parseDate(dynamic v) =>
    v == null ? null : DateTime.tryParse(v.toString())?.toLocal();

// ─── BackupJob ────────────────────────────────────────────────────────────────

class BackupJob {
  BackupJob({
    required this.jobId,
    required this.backupId,
    required this.serverId,
    required this.status,
    required this.phase,
    required this.progressPercent,
    required this.progressBytes,
    required this.totalBytes,
    this.errorCode,
    this.errorMessage,
    required this.createdAt,
    this.startedAt,
    this.completedAt,
    required this.alreadyRunning,
  });

  final String jobId;
  final String backupId;
  final String serverId;
  final BackupStatus status;
  final String phase;
  final int progressPercent;
  final int progressBytes;
  final int totalBytes;
  final String? errorCode;
  final String? errorMessage;
  final DateTime createdAt;
  final DateTime? startedAt;
  final DateTime? completedAt;
  final bool alreadyRunning;

  bool get isActive =>
      status == BackupStatus.pending ||
      status == BackupStatus.running ||
      status == BackupStatus.copying ||
      status == BackupStatus.compressing ||
      status == BackupStatus.verifying;

  bool get isCompleted => status == BackupStatus.completed;
  bool get isFailed => status == BackupStatus.failed;

  factory BackupJob.fromJson(Map<String, dynamic> j) => BackupJob(
        jobId: j['job_id'].toString(),
        backupId: j['backup_id'].toString(),
        serverId: j['server_id'].toString(),
        status: _backupStatusFromString(j['status'].toString()),
        phase: j['phase']?.toString() ?? '',
        progressPercent: (j['progress_percent'] as num?)?.toInt() ?? 0,
        progressBytes: (j['progress_bytes'] as num?)?.toInt() ?? 0,
        totalBytes: (j['total_bytes'] as num?)?.toInt() ?? 0,
        errorCode: j['error_code']?.toString(),
        errorMessage: j['error_message']?.toString(),
        createdAt: _parseDate(j['created_at']) ?? DateTime.now(),
        startedAt: _parseDate(j['started_at']),
        completedAt: _parseDate(j['completed_at']),
        alreadyRunning: j['already_running'] == true,
      );
}

// ─── Backup ───────────────────────────────────────────────────────────────────

class Backup {
  Backup({
    required this.backupId,
    required this.serverId,
    this.name,
    this.description,
    required this.kind,
    required this.status,
    required this.worldName,
    required this.archiveFormat,
    required this.sizeBytes,
    required this.uncompressedSizeBytes,
    this.checksumSha256,
    this.consistencyMethod,
    required this.serverWasRunning,
    required this.pinned,
    required this.createdAt,
    this.completedAt,
    this.failureCode,
    this.failureMessage,
  });

  final String backupId;
  final String serverId;
  final String? name;
  final String? description;
  final BackupKind kind;
  final BackupStatus status;
  final String worldName;
  final String archiveFormat;
  final int sizeBytes;
  final int uncompressedSizeBytes;
  final String? checksumSha256;
  final String? consistencyMethod;
  final bool serverWasRunning;
  final bool pinned;
  final DateTime createdAt;
  final DateTime? completedAt;
  final String? failureCode;
  final String? failureMessage;

  String get displayName =>
      (name != null && name!.isNotEmpty) ? name! : 'Backup ${_shortId(backupId)}';

  String _shortId(String id) => id.length > 8 ? id.substring(0, 8) : id;

  String get displaySize {
    if (sizeBytes <= 0) return '—';
    if (sizeBytes < 1024) return '${sizeBytes}B';
    if (sizeBytes < 1024 * 1024) return '${(sizeBytes / 1024).toStringAsFixed(1)}KB';
    if (sizeBytes < 1024 * 1024 * 1024) {
      return '${(sizeBytes / (1024 * 1024)).toStringAsFixed(1)}MB';
    }
    return '${(sizeBytes / (1024 * 1024 * 1024)).toStringAsFixed(2)}GB';
  }

  factory Backup.fromJson(Map<String, dynamic> j) => Backup(
        backupId: j['backup_id'].toString(),
        serverId: j['server_id'].toString(),
        name: j['name']?.toString(),
        description: j['description']?.toString(),
        kind: _backupKindFromString(j['kind']?.toString() ?? 'manual'),
        status: _backupStatusFromString(j['status']?.toString() ?? 'pending'),
        worldName: j['world_name']?.toString() ?? '',
        archiveFormat: j['archive_format']?.toString() ?? '',
        sizeBytes: (j['size_bytes'] as num?)?.toInt() ?? 0,
        uncompressedSizeBytes:
            (j['uncompressed_size_bytes'] as num?)?.toInt() ?? 0,
        checksumSha256: j['checksum_sha256']?.toString(),
        consistencyMethod: j['consistency_method']?.toString(),
        serverWasRunning: j['server_was_running'] == true,
        pinned: j['pinned'] == true,
        createdAt: _parseDate(j['created_at']) ?? DateTime.now(),
        completedAt: _parseDate(j['completed_at']),
        failureCode: j['failure_code']?.toString(),
        failureMessage: j['failure_message']?.toString(),
      );
}

// ─── RestoreJob ───────────────────────────────────────────────────────────────

class RestoreJob {
  RestoreJob({
    required this.restoreJobId,
    required this.backupId,
    required this.serverId,
    required this.status,
    required this.phase,
    required this.progressPercent,
    required this.progressBytes,
    required this.totalBytes,
    required this.restartAfterRestore,
    required this.rollbackEnabled,
    this.errorCode,
    this.errorMessage,
    required this.createdAt,
    this.startedAt,
    this.completedAt,
  });

  final String restoreJobId;
  final String backupId;
  final String serverId;
  final RestoreStatus status;
  final String phase;
  final int progressPercent;
  final int progressBytes;
  final int totalBytes;
  final bool restartAfterRestore;
  final bool rollbackEnabled;
  final String? errorCode;
  final String? errorMessage;
  final DateTime createdAt;
  final DateTime? startedAt;
  final DateTime? completedAt;

  bool get isActive =>
      status == RestoreStatus.pending ||
      status == RestoreStatus.running ||
      status == RestoreStatus.stopping_server ||
      status == RestoreStatus.stopping ||
      status == RestoreStatus.extracting ||
      status == RestoreStatus.applying ||
      status == RestoreStatus.restarting;

  bool get isCompleted => status == RestoreStatus.completed;
  bool get isFailed =>
      status == RestoreStatus.failed || status == RestoreStatus.rolled_back;

  factory RestoreJob.fromJson(Map<String, dynamic> j) => RestoreJob(
        restoreJobId: j['restore_job_id'].toString(),
        backupId: j['backup_id'].toString(),
        serverId: j['server_id'].toString(),
        status: _restoreStatusFromString(j['status']?.toString() ?? 'pending'),
        phase: j['phase']?.toString() ?? '',
        progressPercent: (j['progress_percent'] as num?)?.toInt() ?? 0,
        progressBytes: (j['progress_bytes'] as num?)?.toInt() ?? 0,
        totalBytes: (j['total_bytes'] as num?)?.toInt() ?? 0,
        restartAfterRestore: j['restart_after_restore'] == true,
        rollbackEnabled: j['rollback_enabled'] == true,
        errorCode: j['error_code']?.toString(),
        errorMessage: j['error_message']?.toString(),
        createdAt: _parseDate(j['created_at']) ?? DateTime.now(),
        startedAt: _parseDate(j['started_at']),
        completedAt: _parseDate(j['completed_at']),
      );
}

// ─── BackupSchedule ───────────────────────────────────────────────────────────

class BackupSchedule {
  BackupSchedule({
    required this.scheduleId,
    required this.serverId,
    required this.enabled,
    this.intervalMinutes,
    required this.retentionCount,
    this.retentionDays,
    required this.skipIfServerOffline,
    required this.deferIfJobActive,
    this.lastRunAt,
    this.lastSuccessAt,
    this.nextRunAt,
    required this.consecutiveFailures,
    required this.createdAt,
    required this.updatedAt,
  });

  final String scheduleId;
  final String serverId;
  final bool enabled;
  final int? intervalMinutes;
  final int retentionCount;
  final int? retentionDays;
  final bool skipIfServerOffline;
  final bool deferIfJobActive;
  final DateTime? lastRunAt;
  final DateTime? lastSuccessAt;
  final DateTime? nextRunAt;
  final int consecutiveFailures;
  final DateTime createdAt;
  final DateTime updatedAt;

  factory BackupSchedule.fromJson(Map<String, dynamic> j) => BackupSchedule(
        scheduleId: j['schedule_id'].toString(),
        serverId: j['server_id'].toString(),
        enabled: j['enabled'] == true,
        intervalMinutes: (j['interval_minutes'] as num?)?.toInt(),
        retentionCount: (j['retention_count'] as num?)?.toInt() ?? 7,
        retentionDays: (j['retention_days'] as num?)?.toInt(),
        skipIfServerOffline: j['skip_if_server_offline'] == true,
        deferIfJobActive: j['defer_if_job_active'] != false,
        lastRunAt: _parseDate(j['last_run_at']),
        lastSuccessAt: _parseDate(j['last_success_at']),
        nextRunAt: _parseDate(j['next_run_at']),
        consecutiveFailures: (j['consecutive_failures'] as num?)?.toInt() ?? 0,
        createdAt: _parseDate(j['created_at']) ?? DateTime.now(),
        updatedAt: _parseDate(j['updated_at']) ?? DateTime.now(),
      );

  /// Returns a human-friendly label for the interval.
  String get intervalLabel {
    final m = intervalMinutes;
    if (m == null) return 'Unknown';
    if (m < 60) return 'Every $m minutes';
    final h = m ~/ 60;
    if (m % 60 == 0) {
      if (h == 24) return 'Daily';
      if (h == 168) return 'Weekly';
      return 'Every ${h}h';
    }
    return 'Every ${h}h ${m % 60}m';
  }
}
