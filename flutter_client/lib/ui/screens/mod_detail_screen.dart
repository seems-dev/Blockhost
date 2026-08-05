import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';

import '../../api/blockhost_api.dart';
import '../../models/mod_models.dart';
import '../theme/tranquil_theme.dart';

// Re-defining design tokens to match the rest of the app
const _mono = 'monospace';
const _surfaceGlass = Color(0x80051923);
const _borderGlow = Color(0x4D64FFDA);
const _textDim = Color(0xFFB0BEC5);
const _errorRed = Color(0xFFFF6B6B);
const _warningAmber = Color(0xFFFFD93D);

class ModDetailScreen extends StatefulWidget {
  final String serverId;
  final BlockHostApi api;
  final String projectId;
  final String initialTitle;

  const ModDetailScreen({
    Key? key,
    required this.serverId,
    required this.api,
    required this.projectId,
    required this.initialTitle,
  }) : super(key: key);

  @override
  _ModDetailScreenState createState() => _ModDetailScreenState();
}

class _ModDetailScreenState extends State<ModDetailScreen> {
  bool _isLoading = true;
  bool _isInstalling = false;
  String? _errorMessage;
  ModProjectDetails? _modDetails;
  ModVersion? _selectedVersion;

  @override
  void initState() {
    super.initState();
    _fetchModDetails();
  }

  Future<void> _fetchModDetails() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });
    try {
      final details = await widget.api.getModDetails(widget.serverId, widget.projectId);
      setState(() {
        _modDetails = details;
        if (details.versions.isNotEmpty) {
          _selectedVersion = details.versions.first;
        }
        _isLoading = false;
      });
    } catch (e) {
      setState(() {
        _errorMessage = 'Failed to load mod details: $e';
        _isLoading = false;
      });
    }
  }

  void _showSnack(String msg, {bool ok = true}) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          msg,
          style: const TextStyle(color: Colors.white, fontFamily: _mono),
        ),
        backgroundColor: ok ? TranquilTheme.glowCyan : _errorRed,
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  Future<void> _installMod() async {
    if (_modDetails == null || _selectedVersion == null) return;

    final isNewest = _selectedVersion == _modDetails!.versions.first;

    // The Version Switching Safety Flow
    if (!isNewest) {
      final proceed = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          backgroundColor: TranquilTheme.deepWater,
          title: Row(
            children: const [
              Icon(Icons.warning_amber_rounded, color: _warningAmber),
              SizedBox(width: 8),
              Text('Compatibility Warning', style: TextStyle(color: Colors.white)),
            ],
          ),
          content: Text(
            'You are selecting version (${_selectedVersion!.versionNumber}).\n\n'
            'Changing mod versions can cause world corruption or plugin conflicts. '
            'An automatic backup will be taken before the switch.',
            style: const TextStyle(color: _textDim),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel', style: TextStyle(color: _textDim)),
            ),
            ElevatedButton(
              onPressed: () => Navigator.pop(ctx, true),
              style: ElevatedButton.styleFrom(backgroundColor: _warningAmber, foregroundColor: Colors.black),
              child: const Text('Backup & Switch'),
            ),
          ],
        ),
      );

      if (proceed != true) return;

      setState(() => _isInstalling = true);
      _showSnack('Taking automatic backup...', ok: true);
      try {
        await widget.api.createBackup(
          widget.serverId,
          name: 'Pre-switch: ${_modDetails!.title} v${_selectedVersion!.versionNumber}',
          description: 'Automatic backup before switching mod version.',
        );
      } catch (e) {
        if (!mounted) return;
        _showSnack('Failed to backup: $e', ok: false);
        setState(() => _isInstalling = false);
        return; // Abort if backup fails
      }
    } else {
      setState(() => _isInstalling = true);
    }

    try {
      await widget.api.installMod(
        widget.serverId,
        ModInstallRequest(
          projectId: _modDetails!.projectId,
          versionId: _selectedVersion!.id,
        ),
      );
      if (!mounted) return;
      _showSnack('${_modDetails!.title} installed! Restart server to activate.', ok: true);
    } catch (e) {
      if (!mounted) return;
      _showSnack('Install failed: $e', ok: false);
    } finally {
      if (mounted) setState(() => _isInstalling = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: Stack(
        children: [
          // ── Background ──────────────────────────────────────────────
          Container(
            decoration: const BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [Color(0xFF0A0F1A), Color(0xFF061520), Color(0xFF021B2A)],
              ),
            ),
          ),
          ClipRect(
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
              child: Container(color: Colors.transparent),
            ),
          ),
          
          SafeArea(
            child: Column(
              children: [
                // ── Header ─────────────────────────────────────────────
                _buildGlassHeader(),
                
                // ── Body ───────────────────────────────────────────────
                Expanded(
                  child: _isLoading
                      ? const Center(child: CircularProgressIndicator(color: TranquilTheme.glowCyan))
                      : _errorMessage != null
                          ? Center(
                              child: Padding(
                                padding: const EdgeInsets.all(20.0),
                                child: Text(_errorMessage!, style: const TextStyle(color: _errorRed), textAlign: TextAlign.center),
                              ),
                            )
                          : _buildModContent(),
                ),
              ],
            ),
          ),
          
          if (_isInstalling)
            Container(
              color: Colors.black54,
              child: const Center(
                child: CircularProgressIndicator(
                  color: TranquilTheme.glowCyan,
                  strokeWidth: 2.5,
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildGlassHeader() {
    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
        child: Container(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 14),
          decoration: BoxDecoration(
            color: TranquilTheme.deepWater.withOpacity(0.4),
            border: Border(bottom: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.2))),
          ),
          child: Row(
            children: [
              GestureDetector(
                onTap: () => Navigator.maybePop(context),
                child: Container(
                  width: 34, height: 34,
                  decoration: BoxDecoration(
                    color: TranquilTheme.glowCyan.withOpacity(0.1),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: TranquilTheme.glowCyan.withOpacity(0.3)),
                  ),
                  child: const Icon(Icons.arrow_back_ios_new_rounded, size: 15, color: TranquilTheme.glowCyan),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  _modDetails?.title ?? widget.initialTitle,
                  style: const TextStyle(color: Colors.white, fontSize: 17, fontWeight: FontWeight.bold),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildModContent() {
    if (_modDetails == null) return const SizedBox.shrink();
    
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Mod Icon and Stats
          Row(
            children: [
              if (_modDetails!.iconUrl != null)
                ClipRRect(
                  borderRadius: BorderRadius.circular(12),
                  child: Image.network(
                    _modDetails!.iconUrl!,
                    width: 80, height: 80,
                    fit: BoxFit.cover,
                  ),
                )
              else
                Container(
                  width: 80, height: 80,
                  decoration: BoxDecoration(
                    color: _surfaceGlass,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: _borderGlow),
                  ),
                  child: const Icon(Icons.extension_rounded, size: 40, color: TranquilTheme.glowCyan),
                ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${_modDetails!.downloads} downloads',
                      style: const TextStyle(color: _textDim, fontSize: 12, fontFamily: _mono),
                    ),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: _modDetails!.categories.take(4).map((c) {
                        return Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                          decoration: BoxDecoration(
                            color: TranquilTheme.glowCyan.withOpacity(0.1),
                            borderRadius: BorderRadius.circular(8),
                            border: Border.all(color: _borderGlow.withOpacity(0.5)),
                          ),
                          child: Text(c, style: const TextStyle(color: TranquilTheme.glowCyan, fontSize: 10)),
                        );
                      }).toList(),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 24),
          
          // Version Selector
          if (_modDetails!.versions.isNotEmpty) ...[
            const Text(
              'Select Version',
              style: TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              decoration: BoxDecoration(
                color: Colors.black26,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: _borderGlow.withOpacity(0.5)),
              ),
              child: DropdownButtonHideUnderline(
                child: DropdownButton<ModVersion>(
                  dropdownColor: TranquilTheme.deepWater,
                  value: _selectedVersion,
                  isExpanded: true,
                  icon: const Icon(Icons.arrow_drop_down, color: TranquilTheme.glowCyan),
                  items: _modDetails!.versions.map((v) {
                    final isLatest = v == _modDetails!.versions.first;
                    return DropdownMenuItem<ModVersion>(
                      value: v,
                      child: Text(
                        '${v.versionNumber} ${isLatest ? "(Latest)" : ""}',
                        style: const TextStyle(color: Colors.white, fontFamily: _mono),
                      ),
                    );
                  }).toList(),
                  onChanged: (val) {
                    if (val != null) setState(() => _selectedVersion = val);
                  },
                ),
              ),
            ),
            const SizedBox(height: 24),
          ],

          // Install Button
          SizedBox(
            height: 48,
            child: ElevatedButton.icon(
              onPressed: _isInstalling ? null : _installMod,
              style: ElevatedButton.styleFrom(
                backgroundColor: TranquilTheme.glowCyan.withOpacity(0.15),
                foregroundColor: TranquilTheme.glowCyan,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(12),
                  side: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.4)),
                ),
                elevation: 0,
              ),
              icon: const Icon(Icons.download_rounded),
              label: Text(
                _selectedVersion == _modDetails!.versions.first ? 'Install Latest' : 'Backup & Install',
                style: const TextStyle(fontWeight: FontWeight.bold, fontFamily: _mono),
              ),
            ),
          ),
          const SizedBox(height: 32),

          // Markdown Body
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: _surfaceGlass,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: _borderGlow),
            ),
            child: MarkdownBody(
              data: _modDetails!.body.isNotEmpty ? _modDetails!.body : _modDetails!.description,
              styleSheet: MarkdownStyleSheet(
                p: const TextStyle(color: Colors.white, fontSize: 14, height: 1.5),
                h1: const TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.bold),
                h2: const TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.bold),
                h3: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.bold),
                a: const TextStyle(color: TranquilTheme.glowCyan, decoration: TextDecoration.underline),
                code: TextStyle(
                  color: TranquilTheme.glowCyan,
                  backgroundColor: TranquilTheme.deepWater.withOpacity(0.5),
                  fontFamily: _mono,
                  fontSize: 13,
                ),
                codeblockDecoration: BoxDecoration(
                  color: TranquilTheme.deepWater.withOpacity(0.5),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: _borderGlow),
                ),
                blockquoteDecoration: BoxDecoration(
                  color: TranquilTheme.glowCyan.withOpacity(0.05),
                  border: Border(left: BorderSide(color: TranquilTheme.glowCyan, width: 3)),
                ),
              ),
            ),
          ),
          const SizedBox(height: 40),
        ],
      ),
    );
  }
}