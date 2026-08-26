enum ServerFlavorType { java, bedrock }

class ServerFlavor {
  final String id;
  final String name;
  final ServerFlavorType type;
  final String icon;
  final bool comingSoon;

  const ServerFlavor({
    required this.id,
    required this.name,
    required this.type,
    required this.icon,
    this.comingSoon = false,
  });
}

class CreateServerPayload {
  final String worldName;
  final String flavor;
  final String? mcVersion;

  CreateServerPayload({
    required this.worldName,
    required this.flavor,
    this.mcVersion,
  });

  Map<String, dynamic> toJson() => {
        'world_name': worldName,
        'flavor': flavor,
        'mc_version': mcVersion,
      };
}