class CustomDomain {
  CustomDomain({
    required this.id,
    required this.deploymentId,
    required this.domain,
    required this.status,
  });

  final String id;
  final String deploymentId;
  final String domain;
  final String status;

  factory CustomDomain.fromJson(Map<String, dynamic> json) {
    return CustomDomain(
      id: json['id'] as String,
      deploymentId: json['deployment_id'] as String,
      domain: json['domain'] as String,
      status: json['status'] as String,
    );
  }
}

class AppDeployment {
  AppDeployment({
    required this.id,
    required this.ownerId,
    this.nodeId,
    required this.name,
    required this.dockerImage,
    required this.internalPort,
    required this.ramLimitMb,
    required this.cpuLimit,
    required this.state,
    this.errorMessage,
    this.containerId,
    this.hostPort,
    this.volumePath,
    this.volumeMountPath,
    this.lastNetworkRx,
    required this.createdAt,
    required this.updatedAt,
    this.customDomains = const [],
  });

  final String id;
  final String ownerId;
  final String? nodeId;
  final String name;
  final String dockerImage;
  final int internalPort;
  final int ramLimitMb;
  final double cpuLimit;
  final String state;
  final String? errorMessage;
  final String? containerId;
  final int? hostPort;
  final String? volumePath;
  final String? volumeMountPath;
  final int? lastNetworkRx;
  final DateTime createdAt;
  final DateTime updatedAt;
  final List<CustomDomain> customDomains;

  factory AppDeployment.fromJson(Map<String, dynamic> json) {
    return AppDeployment(
      id: json['id'] as String,
      ownerId: json['owner_id'] as String,
      nodeId: json['node_id'] as String?,
      name: json['name'] as String,
      dockerImage: json['docker_image'] as String,
      internalPort: json['internal_port'] as int,
      ramLimitMb: json['ram_limit_mb'] as int,
      cpuLimit: (json['cpu_limit'] as num).toDouble(),
      state: json['state'] as String,
      errorMessage: json['error_message'] as String?,
      containerId: json['container_id'] as String?,
      hostPort: json['host_port'] as int?,
      volumePath: json['volume_path'] as String?,
      volumeMountPath: json['volume_mount_path'] as String?,
      lastNetworkRx: json['last_network_rx'] as int?,
      createdAt: DateTime.parse(json['created_at'] as String),
      updatedAt: DateTime.parse(json['updated_at'] as String),
      customDomains: (json['custom_domains'] as List? ?? [])
          .map((e) => CustomDomain.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }

  bool get isRunning => state == 'running';
  bool get isSuspended => state == 'suspended';
  bool get isError => state == 'error';
  bool get isBuilding => state == 'building' || state == 'created';
}
