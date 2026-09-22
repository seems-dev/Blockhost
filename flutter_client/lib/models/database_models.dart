class HostingProject {
  HostingProject({
    required this.id,
    required this.ownerId,
    required this.name,
    required this.createdAt,
  });

  final String id;
  final String ownerId;
  final String name;
  final DateTime createdAt;

  factory HostingProject.fromJson(Map<String, dynamic> json) {
    return HostingProject(
      id: json['id'] as String,
      ownerId: json['owner_id'] as String,
      name: json['name'] as String,
      createdAt: DateTime.parse(json['created_at'] as String),
    );
  }
}

class DatabaseInstance {
  DatabaseInstance({
    required this.id,
    required this.projectId,
    required this.name,
    required this.engine,
    required this.version,
    required this.internalHostname,
    required this.internalPort,
    required this.dbName,
    required this.dbUsername,
    required this.state,
    required this.createdAt,
    this.dbPassword,
    this.connectionUrl,
  });

  final String id;
  final String projectId;
  final String name;
  final String engine;
  final String version;
  final String internalHostname;
  final int internalPort;
  final String dbName;
  final String dbUsername;
  final String state;
  final DateTime createdAt;
  final String? dbPassword;
  final String? connectionUrl;

  factory DatabaseInstance.fromJson(Map<String, dynamic> json) {
    return DatabaseInstance(
      id: json['id'] as String,
      projectId: json['project_id'] as String,
      name: json['name'] as String,
      engine: json['engine'] as String,
      version: json['version'] as String,
      internalHostname: json['internal_hostname'] as String,
      internalPort: json['internal_port'] as int,
      dbName: json['db_name'] as String,
      dbUsername: json['db_username'] as String,
      state: json['state'] as String,
      createdAt: DateTime.parse(json['created_at'] as String),
      dbPassword: json['db_password'] as String?,
      connectionUrl: json['connection_url'] as String?,
    );
  }
}
