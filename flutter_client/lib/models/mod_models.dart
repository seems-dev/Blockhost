class ModCapability {
  final bool supported;
  final List<String> loaders;
  final String installSubdir;

  ModCapability({
    required this.supported,
    required this.loaders,
    required this.installSubdir,
  });

  factory ModCapability.fromJson(Map<String, dynamic> json) {
    return ModCapability(
      supported: json['supported'] ?? false,
      loaders: List<String>.from(json['loaders'] ?? []),
      installSubdir: json['install_subdir'] ?? 'mods',
    );
  }
}

class ModSearchResult {
  final String projectId;
  final String slug;
  final String title;
  final String description;
  final String? iconUrl;
  final int downloads;
  final String? latestVersionId;

  ModSearchResult({
    required this.projectId,
    required this.slug,
    required this.title,
    required this.description,
    this.iconUrl,
    required this.downloads,
    this.latestVersionId,
  });

  factory ModSearchResult.fromJson(Map<String, dynamic> json) {
    return ModSearchResult(
      projectId: json['project_id'] ?? '',
      slug: json['slug'] ?? '',
      title: json['title'] ?? 'Unknown Mod',
      description: json['description'] ?? '',
      iconUrl: json['icon_url'],
      downloads: json['downloads'] ?? 0,
      latestVersionId: json['latest_version_id'],
    );
  }
}

class ModInstallRequest {
  final String projectId;
  final String? versionId;

  ModInstallRequest({
    required this.projectId,
    this.versionId,
  });

  Map<String, dynamic> toJson() {
    return {
      'modrinth_project_id': projectId,
      if (versionId != null) 'version_id': versionId,
    };
  }
}

/// A specific version entry from Modrinth.
class ModVersion {
  final String id;
  final String name;
  final String versionNumber;
  final String datePublished;
  final int downloads;

  ModVersion({
    required this.id,
    required this.name,
    required this.versionNumber,
    required this.datePublished,
    required this.downloads,
  });

  factory ModVersion.fromJson(Map<String, dynamic> json) {
    return ModVersion(
      id: json['id'] ?? '',
      name: json['name'] ?? json['id'] ?? '',
      versionNumber: json['version_number'] ?? '',
      datePublished: json['date_published'] ?? '',
      downloads: json['downloads'] ?? 0,
    );
  }
}

/// Full project details returned by the detail endpoint.
class InstalledMod {
  final String filename;

  InstalledMod({required this.filename});

  factory InstalledMod.fromJson(Map<String, dynamic> json) {
    return InstalledMod(filename: json['filename']?.toString() ?? '');
  }
}

class ModProjectDetails {
  final String projectId;
  final String slug;
  final String title;
  final String description;
  final String body;
  final String? iconUrl;
  final int downloads;
  final List<String> categories;
  final List<ModVersion> versions;
  final String? latestVersionId;

  ModProjectDetails({
    required this.projectId,
    required this.slug,
    required this.title,
    required this.description,
    required this.body,
    this.iconUrl,
    required this.downloads,
    required this.categories,
    required this.versions,
    this.latestVersionId,
  });

  factory ModProjectDetails.fromJson(Map<String, dynamic> json) {
    final versions = (json['versions'] as List? ?? [])
        .map((v) => ModVersion.fromJson(v as Map<String, dynamic>))
        .toList();

    return ModProjectDetails(
      projectId: json['project_id'] ?? '',
      slug: json['slug'] ?? '',
      title: json['title'] ?? '',
      description: json['description'] ?? '',
      body: json['body'] ?? '',
      iconUrl: json['icon_url'],
      downloads: json['downloads'] ?? 0,
      categories: List<String>.from(json['categories'] ?? []),
      versions: versions,
      latestVersionId: json['latest_version_id']?.toString() ??
          (versions.isNotEmpty ? versions.first.id : null),
    );
  }
}