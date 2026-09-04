import 'package:url_launcher/url_launcher.dart';

class MinecraftLauncher {
  static Uri serverUri({
    required String serverName,
    required String address,
  }) {
    final safeName = serverName.replaceAll('|', ' ').trim();
    final safeAddress = address.trim();
    return Uri.parse(
      'minecraft://?addExternalServer=${Uri.encodeComponent('${safeName.isEmpty ? 'Erex Server' : safeName}|$safeAddress')}',
    );
  }

  static Future<bool> launchServer({
    required String serverName,
    required String address,
  }) async {
    if (address.trim().isEmpty || address == 'N/A') return false;

    final uri = serverUri(serverName: serverName, address: address);
    if (!await canLaunchUrl(uri)) return false;

    return launchUrl(uri, mode: LaunchMode.externalApplication);
  }
}
