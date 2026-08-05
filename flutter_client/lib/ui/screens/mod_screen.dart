import 'dart:ui';
import 'package:flutter/material.dart';

import '../../api/blockhost_api.dart';
import '../../models/mod_models.dart';
import '../../state/app_state.dart';
import '../theme/tranquil_theme.dart';
import 'mod_detail_screen.dart';
// ─── Design tokens (matching the rest of the app) ─────────────────────────────
const _mono = 'monospace';
const _errorRed = Color(0xFFFF6B6B);
const _surfaceGlass = Color(0x80051923);
const _borderGlow = Color(0x4D64FFDA);
const _textDim = Color(0xFFB0BEC5);

class ModsScreen extends StatefulWidget {
  final String serverId;
  final AppState state;

  const ModsScreen({Key? key, required this.serverId, required this.state})
      : super(key: key);

  @override
  _ModsScreenState createState() => _ModsScreenState();
}

class _ModsScreenState extends State<ModsScreen>
    with SingleTickerProviderStateMixin {
  late TabController _tabController;
  final TextEditingController _searchController = TextEditingController();

  bool _isLoading = false;
  bool _isInstalling = false;
  String? _errorMessage;

  ModCapability? _capability;
  List<ModSearchResult> _searchResults = [];
  List<InstalledMod> _installedMods = [];
  BlockHostApi get _api => widget.state.api;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this);
    _checkCapability();
  }

  @override
  void dispose() {
    _tabController.dispose();
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _checkCapability() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });
    try {
      final capability = await _api.getModCapability(widget.serverId);
      if (!mounted) return;
      setState(() {
        _capability = capability;
        _isLoading = false;
      });
      if (capability.supported) _fetchInstalledMods();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = 'Failed to check mod support: $e';
        _isLoading = false;
      });
    }
  }

  Future<void> _searchMods() async {
    final q = _searchController.text.trim();
    if (q.isEmpty) return;
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });
    try {
      final results = await _api.searchMods(widget.serverId, q);
      if (!mounted) return;
      setState(() {
        _searchResults = results;
        _isLoading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = 'Search failed: $e';
        _isLoading = false;
      });
    }
  }

  Future<void> _fetchInstalledMods() async {
    setState(() => _isLoading = true);
    try {
      final mods = await _api.listInstalledMods(widget.serverId);
      if (!mounted) return;
      setState(() {
        _installedMods = mods;
        _isLoading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = 'Failed to load installed mods: $e';
        _isLoading = false;
      });
    }
  }

  Future<void> _installMod(ModSearchResult mod) async {
    setState(() => _isInstalling = true);
    try {
      await _api.installMod(
        widget.serverId,
        ModInstallRequest(projectId: mod.projectId),
      );
      if (!mounted) return;
      _showSnack('${mod.title} installed! Restart server to activate.', ok: true);
      _fetchInstalledMods();
    } on ApiException catch (e) {
      if (!mounted) return;
      _showSnack('Failed to install ${mod.title}: ${e.message}', ok: false);
    } catch (e) {
      if (!mounted) return;
      _showSnack('Failed to install ${mod.title}: $e', ok: false);
    } finally {
      if (mounted) setState(() => _isInstalling = false);
    }
  }

    Future<void> _deleteMod(InstalledMod mod) async {
    try {
      await _api.deleteMod(widget.serverId, mod.filename);
      if (!mounted) return;
      setState(() => _installedMods.remove(mod));
      _showSnack('${mod.filename} deleted.', ok: true);
    } catch (e) {
      if (!mounted) return;
      _showSnack('Failed to delete ${mod.filename}: $e', ok: false);
    }
  }

  void _showSnack(String msg, {required bool ok}) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(msg, style: const TextStyle(fontFamily: _mono)),
      backgroundColor: ok
          ? TranquilTheme.glowCyan.withOpacity(0.9)
          : _errorRed.withOpacity(0.9),
      behavior: SnackBarBehavior.floating,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
    ));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0A0F1A),
      body: Stack(
        children: [
          // ── Background gradient ─────────────────────────────────────────
          Container(
            decoration: const BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [Color(0xFF0A0F1A), Color(0xFF061520), Color(0xFF021B2A)],
              ),
            ),
          ),
          SafeArea(
            child: Column(
              children: [
                _buildHeader(),
                _buildTabBar(),
                Expanded(child: _buildBody()),
              ],
            ),
          ),
          if (_isInstalling)
            Container(
              color: Colors.black54,
              child: Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    CircularProgressIndicator(color: TranquilTheme.glowCyan),
                    const SizedBox(height: 16),
                    Text(
                      'Installing mod…',
                      style: TextStyle(
                        color: TranquilTheme.glowCyan,
                        fontFamily: _mono,
                        fontSize: 13,
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildHeader() {
    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
        child: Container(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 14),
          decoration: BoxDecoration(
            color: const Color(0xFF051923).withOpacity(0.6),
            border: Border(
              bottom: BorderSide(
                color: TranquilTheme.glowCyan.withOpacity(0.2),
              ),
            ),
          ),
          child: Row(
            children: [
              GestureDetector(
                onTap: () => Navigator.maybePop(context),
                child: Container(
                  width: 34,
                  height: 34,
                  decoration: BoxDecoration(
                    color: TranquilTheme.glowCyan.withOpacity(0.1),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(
                        color: TranquilTheme.glowCyan.withOpacity(0.3)),
                  ),
                  child: Icon(Icons.arrow_back_ios_new_rounded,
                      size: 15, color: TranquilTheme.glowCyan),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.extension_rounded,
                            color: TranquilTheme.glowCyan, size: 16),
                        const SizedBox(width: 8),
                        const Text(
                          'Mod Manager',
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 17,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 2),
                    if (_capability != null)
                      Text(
                        _capability!.supported
                            ? 'Powered by Modrinth · ${_capability!.loaders.join(", ")}'
                            : 'Not available for this server type',
                        style: TextStyle(
                          color: _textDim,
                          fontSize: 11,
                          fontFamily: _mono,
                        ),
                      ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildTabBar() {
    return Container(
      decoration: BoxDecoration(
        color: _surfaceGlass,
        border: Border(
          bottom: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.15)),
        ),
      ),
      child: TabBar(
        controller: _tabController,
        indicatorColor: TranquilTheme.glowCyan,
        indicatorWeight: 2,
        labelColor: TranquilTheme.glowCyan,
        unselectedLabelColor: _textDim,
        labelStyle: const TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w700,
          fontFamily: _mono,
          letterSpacing: 0.5,
        ),
        tabs: const [
          Tab(text: 'SEARCH & INSTALL'),
          Tab(text: 'INSTALLED'),
        ],
      ),
    );
  }

  Widget _buildBody() {
    if (_isLoading && _capability == null) {
      return Center(
        child: CircularProgressIndicator(color: TranquilTheme.glowCyan),
      );
    }

    if (_errorMessage != null && _capability == null) {
      return _buildErrorView(_errorMessage!);
    }

    if (_capability == null || !_capability!.supported) {
      return _buildUnsupportedView();
    }

    return TabBarView(
      controller: _tabController,
      children: [
        _buildSearchTab(),
        _buildInstalledTab(),
      ],
    );
  }

  Widget _buildUnsupportedView() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Container(
          padding: const EdgeInsets.all(28),
          decoration: BoxDecoration(
            color: _surfaceGlass,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: _errorRed.withOpacity(0.3)),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.extension_off_rounded, size: 48, color: _errorRed),
              const SizedBox(height: 16),
              const Text(
                'Mods Not Supported',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                _capability?.supported == false
                    ? 'This server type doesn\'t support mod management via Modrinth.'
                    : 'Mod support is not available for this server.',
                textAlign: TextAlign.center,
                style: TextStyle(color: _textDim, fontSize: 13),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildErrorView(String error) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.error_outline_rounded, size: 48, color: _errorRed),
            const SizedBox(height: 16),
            Text(error,
                textAlign: TextAlign.center,
                style: TextStyle(color: _textDim, fontFamily: _mono)),
            const SizedBox(height: 16),
            GestureDetector(
              onTap: _checkCapability,
              child: Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
                decoration: BoxDecoration(
                  color: TranquilTheme.glowCyan.withOpacity(0.1),
                  borderRadius: BorderRadius.circular(10),
                  border:
                      Border.all(color: TranquilTheme.glowCyan.withOpacity(0.3)),
                ),
                child: Text('Retry',
                    style: TextStyle(
                        color: TranquilTheme.glowCyan, fontFamily: _mono)),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // ─── Search Tab ────────────────────────────────────────────────────────────

  Widget _buildSearchTab() {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
          child: Row(
            children: [
              Expanded(
                child: Container(
                  decoration: BoxDecoration(
                    color: const Color(0xFF051923).withOpacity(0.5),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(
                        color: TranquilTheme.glowCyan.withOpacity(0.3)),
                  ),
                  child: TextField(
                    controller: _searchController,
                    style: const TextStyle(
                        color: Colors.white, fontSize: 13, fontFamily: _mono),
                    decoration: InputDecoration(
                      hintText: 'Search Modrinth…',
                      hintStyle:
                          TextStyle(color: _textDim, fontFamily: _mono, fontSize: 12),
                      prefixIcon: Icon(Icons.search_rounded,
                          color: TranquilTheme.glowCyan, size: 20),
                      border: InputBorder.none,
                      contentPadding: const EdgeInsets.symmetric(
                          vertical: 14, horizontal: 4),
                    ),
                    onSubmitted: (_) => _searchMods(),
                  ),
                ),
              ),
              const SizedBox(width: 10),
              GestureDetector(
                onTap: _isLoading ? null : _searchMods,
                child: Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    color: TranquilTheme.glowCyan.withOpacity(0.1),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(
                        color: TranquilTheme.glowCyan.withOpacity(0.4)),
                  ),
                  child: Icon(Icons.send_rounded,
                      color: TranquilTheme.glowCyan, size: 18),
                ),
              ),
            ],
          ),
        ),
        if (_isLoading)
          LinearProgressIndicator(
            color: TranquilTheme.glowCyan,
            backgroundColor: TranquilTheme.glowCyan.withOpacity(0.1),
          ),
        if (_errorMessage != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: _errorRed.withOpacity(0.08),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: _errorRed.withOpacity(0.25)),
              ),
              child: Row(
                children: [
                  Icon(Icons.error_outline_rounded, color: _errorRed, size: 14),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(_errorMessage!,
                        style: TextStyle(
                            color: _errorRed, fontSize: 11, fontFamily: _mono)),
                  ),
                ],
              ),
            ),
          ),
        Expanded(
          child: _searchResults.isEmpty
              ? _buildSearchEmptyState()
              : ListView.separated(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
                  itemCount: _searchResults.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 8),
                  itemBuilder: (_, i) => GestureDetector(
                    onTap: () {
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => ModDetailScreen(
                            serverId: widget.serverId,
                            api: _api,
                            projectId: _searchResults[i].projectId,
                            initialTitle: _searchResults[i].title,
                          ),
                        ),
                      );
                    },
                    child: _ModCard(
                      mod: _searchResults[i],
                      isInstalling: _isInstalling,
                      onInstall: () => _installMod(_searchResults[i]),
                    ),
                  ),
                ),
        ),
      ],
    );
  }

  Widget _buildSearchEmptyState() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.search_rounded, size: 48, color: _borderGlow),
          const SizedBox(height: 12),
          Text(
            'Search Modrinth for mods',
            style: TextStyle(color: _textDim, fontFamily: _mono, fontSize: 13),
          ),
          const SizedBox(height: 4),
          Text(
            'Results are filtered to your server\'s version',
            style: TextStyle(color: _textDim.withOpacity(0.6), fontSize: 11),
          ),
        ],
      ),
    );
  }

  // ─── Installed Tab ─────────────────────────────────────────────────────────

  Widget _buildInstalledTab() {
    return RefreshIndicator(
      color: TranquilTheme.glowCyan,
      backgroundColor: const Color(0xFF051923),
      onRefresh: _fetchInstalledMods,
      child: _installedMods.isEmpty
          ? ListView(
              children: [
                const SizedBox(height: 80),
                Center(
                  child: Column(
                    children: [
                      Icon(Icons.inbox_rounded, size: 48, color: _borderGlow),
                      const SizedBox(height: 12),
                      Text(
                        'No mods installed',
                        style: TextStyle(
                            color: _textDim, fontFamily: _mono, fontSize: 13),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'Pull to refresh',
                        style: TextStyle(
                            color: _textDim.withOpacity(0.5), fontSize: 11),
                      ),
                    ],
                  ),
                ),
              ],
            )
          : ListView.separated(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
              itemCount: _installedMods.length,
              separatorBuilder: (_, __) => const SizedBox(height: 8),
              itemBuilder: (_, i) => _InstalledModTile(
                filename: _installedMods[i].filename,
                onDelete: () => _deleteMod(_installedMods[i]),
              ),
            ),
    );
  }
}

// ─── Sub-widgets ───────────────────────────────────────────────────────────────

class _ModCard extends StatelessWidget {
  const _ModCard({
    required this.mod,
    required this.isInstalling,
    required this.onInstall,
  });

  final ModSearchResult mod;
  final bool isInstalling;
  final VoidCallback onInstall;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: _surfaceGlass,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: _borderGlow),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Icon
          ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: mod.iconUrl != null
                ? Image.network(
                    mod.iconUrl!,
                    width: 44,
                    height: 44,
                    errorBuilder: (_, __, ___) => _fallbackIcon(),
                  )
                : _fallbackIcon(),
          ),
          const SizedBox(width: 12),
          // Info
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  mod.title,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  mod.description,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: _textDim, fontSize: 11, height: 1.3),
                ),
                const SizedBox(height: 6),
                Row(
                  children: [
                    Icon(Icons.download_rounded, size: 11, color: _textDim),
                    const SizedBox(width: 3),
                    Text(
                      _formatDownloads(mod.downloads),
                      style: TextStyle(
                          color: _textDim, fontSize: 10, fontFamily: _mono),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          // Install button
          GestureDetector(
            onTap: isInstalling ? null : onInstall,
            child: Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: TranquilTheme.glowCyan.withOpacity(0.1),
                borderRadius: BorderRadius.circular(8),
                border:
                    Border.all(color: TranquilTheme.glowCyan.withOpacity(0.35)),
              ),
              child: Icon(Icons.download_rounded,
                  size: 18, color: TranquilTheme.glowCyan),
            ),
          ),
        ],
      ),
    );
  }

  Widget _fallbackIcon() => Container(
        width: 44,
        height: 44,
        decoration: BoxDecoration(
          color: TranquilTheme.glowCyan.withOpacity(0.08),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: _borderGlow),
        ),
        child: Icon(Icons.extension_rounded,
            size: 22, color: TranquilTheme.glowCyan.withOpacity(0.6)),
      );

  String _formatDownloads(int n) {
    if (n >= 1000000) return '${(n / 1000000).toStringAsFixed(1)}M';
    if (n >= 1000) return '${(n / 1000).toStringAsFixed(0)}K';
    return '$n';
  }
}

class _InstalledModTile extends StatelessWidget {
  const _InstalledModTile({required this.filename, required this.onDelete});
  final String filename;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: _surfaceGlass,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: _borderGlow),
      ),
      child: Row(
        children: [
          Container(
            width: 36,
            height: 36,
            decoration: BoxDecoration(
              color: TranquilTheme.glowCyan.withOpacity(0.07),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: _borderGlow),
            ),
            child: Icon(Icons.insert_drive_file_rounded,
                size: 18, color: TranquilTheme.glowCyan.withOpacity(0.7)),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  filename,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 12,
                    fontFamily: _mono,
                    fontWeight: FontWeight.w600,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 2),
                Text(
                  '.jar file',
                  style:
                      TextStyle(color: _textDim, fontSize: 10, fontFamily: _mono),
                ),
              ],
            ),
          ),
          GestureDetector(
            onTap: onDelete,
            child: Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: _errorRed.withOpacity(0.08),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: _errorRed.withOpacity(0.3)),
              ),
              child: Icon(Icons.delete_outline_rounded,
                  size: 16, color: _errorRed),
            ),
          ),
        ],
      ),
    );
  }
}