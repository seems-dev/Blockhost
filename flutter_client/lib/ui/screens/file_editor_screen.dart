import 'package:flutter/material.dart';
import 'dart:ui';
import '../../api/erex_api.dart';
import '../../state/app_state.dart';

// ── VS Code Inspired Design Tokens ────────────────────────────────────────────
const _bg              = Color(0xFF0D0F14);
const _editorBg        = Color(0xFF1E1E2E);      // VS Code dark editor background
const _sideBarBg       = Color(0xFF181825);       // Sidebar background
const _statusBarBg     = Color(0xFF181825);       // Status bar background
const _surface         = Color(0xFF1A1B26);       // Surface color
const _card            = Color(0xFF1F2330);
const _border          = Color(0xFF313244);
const _green           = Color(0xFFA6E3A1);       // VS Code green
const _cyan            = Color(0xFF89DCEB);       // VS Code cyan
const _blue            = Color(0xFF89B4FA);       // VS Code blue
const _pink            = Color(0xFFF5C2E7);       // VS Code pink/purple
const _orange          = Color(0xFFFAB387);       // VS Code orange
const _yellow          = Color(0xFFF9E2AF);       // VS Code yellow
const _textPri         = Color(0xFFCDD6F4);       // Main text
const _textMuted       = Color(0xFF6C7086);       // Muted text
const _textDim         = Color(0xFF45475A);       // Dim text
const _lineNumber      = Color(0xFF585B70);       // Line number color
const _selectionColor  = Color(0xFF313244);       // Selection background
const _searchHighlight = Color(0xFFF9E2AF);       // Search highlight
const _errorRed        = Color(0xFFF38BA8);       // Error red

// Syntax highlighting colors
const _keywordColor    = Color(0xFFC678DD);       // Purple for keywords
const _stringColor     = Color(0xFF98C379);       // Green for strings
const _commentColor    = Color(0xFF5C6370);       // Gray for comments
const _numberColor     = Color(0xFFD19A66);       // Orange for numbers
const _functionColor   = Color(0xFF61AFEF);       // Blue for functions

class FileEditorScreen extends StatefulWidget {
  const FileEditorScreen({
    super.key,
    required this.state,
    required this.serverId,
    required this.path,
  });

  final AppState state;
  final String serverId;
  final String path;

  @override
  State<FileEditorScreen> createState() => _FileEditorScreenState();
}

class _FileEditorScreenState extends State<FileEditorScreen> with SingleTickerProviderStateMixin {
  final _textCtrl = TextEditingController();
  final _editorScrollCtrl = ScrollController();
  final _lineNumberScrollCtrl = ScrollController();
  final _focusNode = FocusNode();
  late AnimationController _animationController;
  
  bool _loading = true;
  bool _saving = false;
  String? _error;
  bool _showLineNumbers = true;
  bool _wordWrap = true;
  String _searchQuery = '';
  int _currentMatchIndex = -1;
  List<int> _matchPositions = [];
  final _searchController = TextEditingController();
  final _searchFocusNode = FocusNode();
  bool _showSearchBar = false;
  
  // For auto-save
  String? _lastSavedContent;
  bool _hasUnsavedChanges = false;

  @override
  void initState() {
    super.initState();
    _animationController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 200),
    );
    _focusNode.addListener(_onFocusChange);
    _textCtrl.addListener(_onTextChange);
    _loadFile();
  }

  @override
  void dispose() {
    _animationController.dispose();
    _focusNode.removeListener(_onFocusChange);
    _focusNode.dispose();
    _textCtrl.removeListener(_onTextChange);
    _textCtrl.dispose();
    _editorScrollCtrl.dispose();
    _lineNumberScrollCtrl.dispose();
    _searchController.dispose();
    _searchFocusNode.dispose();
    super.dispose();
  }

  void _onFocusChange() {
    setState(() {});
  }

  void _onTextChange() {
    if (_hasUnsavedChanges == false && _lastSavedContent != null) {
      setState(() => _hasUnsavedChanges = _textCtrl.text != _lastSavedContent);
    } else if (_hasUnsavedChanges && _lastSavedContent != null) {
      setState(() => _hasUnsavedChanges = _textCtrl.text != _lastSavedContent);
    }
  }

  Future<void> _loadFile() async {
    setState(() { _loading = true; _error = null; });
    try {
      final content = await widget.state.api.readFile(widget.serverId, widget.path);
      _textCtrl.text = content;
      _lastSavedContent = content;
      _hasUnsavedChanges = false;
    } on ApiException catch (e) {
      _error = e.message;
    } catch (e) {
      _error = e.toString();
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _saveFile({bool showSnackbar = true}) async {
    if (!_hasUnsavedChanges && showSnackbar) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('No changes to save'), backgroundColor: _textMuted),
        );
      }
      return;
    }
    
    setState(() => _saving = true);
    try {
      await widget.state.api.writeFile(widget.serverId, widget.path, _textCtrl.text);
      _lastSavedContent = _textCtrl.text;
      _hasUnsavedChanges = false;
      
      if (mounted && showSnackbar) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Row(children: const [
              Icon(Icons.check_circle_rounded, color: _green, size: 20),
              SizedBox(width: 12),
              Text('File saved successfully'),
            ]),
            backgroundColor: _surface,
            behavior: SnackBarBehavior.floating,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
          ),
        );
      }
      
      if (showSnackbar) {
        Navigator.pop(context, true);
      }
    } on ApiException catch (e) {
      if (mounted && showSnackbar) {
        _showErrorSnackbar('Save failed: ${e.message}');
      }
    } catch (e) {
      if (mounted && showSnackbar) {
        _showErrorSnackbar('Save failed: $e');
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _showErrorSnackbar(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Row(children: [
          const Icon(Icons.error_outline_rounded, color: _errorRed, size: 20),
          const SizedBox(width: 12),
          Expanded(child: Text(message)),
        ]),
        backgroundColor: _surface,
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),
    );
  }

  void _performSearch() {
    if (_searchQuery.isEmpty) {
      setState(() {
        _matchPositions = [];
        _currentMatchIndex = -1;
      });
      return;
    }
    
    final text = _textCtrl.text;
    final matches = <int>[];
    int index = 0;
    while (index < text.length) {
      final found = text.indexOf(_searchQuery, index);
      if (found == -1) break;
      matches.add(found);
      index = found + _searchQuery.length;
    }
    
    setState(() {
      _matchPositions = matches;
      _currentMatchIndex = matches.isNotEmpty ? 0 : -1;
    });
    
    if (matches.isNotEmpty) {
      _scrollToMatch(matches[0]);
    }
  }

  void _scrollToMatch(int position) {
    final controller = _textCtrl;
    controller.selection = TextSelection(baseOffset: position, extentOffset: position + _searchQuery.length);
    // Note: In a real implementation, you'd need a ScrollController
  }

  void _nextMatch() {
    if (_matchPositions.isEmpty) return;
    final nextIndex = (_currentMatchIndex + 1) % _matchPositions.length;
    setState(() => _currentMatchIndex = nextIndex);
    _scrollToMatch(_matchPositions[nextIndex]);
  }

  void _previousMatch() {
    if (_matchPositions.isEmpty) return;
    final prevIndex = _currentMatchIndex - 1;
    if (prevIndex < 0) return;
    setState(() => _currentMatchIndex = prevIndex);
    _scrollToMatch(_matchPositions[prevIndex]);
  }

  String _getFileLanguage() {
    final extension = widget.path.split('.').last.toLowerCase();
    switch (extension) {
      case 'properties': return 'properties';
      case 'json': return 'json';
      case 'yml': return 'yaml';
      case 'yaml': return 'yaml';
      case 'txt': return 'text';
      case 'md': return 'markdown';
      case 'log': return 'log';
      default: return 'text';
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      body: Column(
        children: [
          // Custom VS Code Style App Bar
          _buildAppBar(),
          
          // Search Bar
          if (_showSearchBar)
            _buildSearchBar(),
          
          // Main Editor Area
          Expanded(child: _buildBody()),
          
          // Status Bar
          _buildStatusBar(),
        ],
      ),
    );
  }

  Widget _buildAppBar() {
    return Container(
      height: 48,
      decoration: BoxDecoration(
        color: _sideBarBg,
        border: Border(bottom: BorderSide(color: _border.withOpacity(0.5))),
      ),
      child: Row(
        children: [
          // File icon and name
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                Icon(Icons.code_rounded, color: _cyan, size: 18),
                const SizedBox(width: 10),
                Text(
                  widget.path.split('/').last,
                  style: const TextStyle(color: _textPri, fontSize: 13, fontWeight: FontWeight.w500),
                ),
                if (_hasUnsavedChanges)
                  Container(
                    margin: const EdgeInsets.only(left: 8),
                    width: 8,
                    height: 8,
                    decoration: const BoxDecoration(color: _orange, shape: BoxShape.circle),
                  ),
              ],
            ),
          ),
          
          const Spacer(),
          
          // Editor actions
          _buildActionButton(
            icon: Icons.search_rounded,
            onTap: () => setState(() => _showSearchBar = !_showSearchBar),
            tooltip: 'Find',
          ),
          _buildActionButton(
            icon: Icons.format_line_spacing_rounded,
            onTap: () => setState(() => _wordWrap = !_wordWrap),
            tooltip: 'Word Wrap',
            active: _wordWrap,
          ),
          _buildActionButton(
            icon: Icons.format_list_numbered_rounded,
            onTap: () => setState(() => _showLineNumbers = !_showLineNumbers),
            tooltip: 'Line Numbers',
            active: _showLineNumbers,
          ),
          
          const SizedBox(width: 8),
          
          // Save button
          if (!_loading && _error == null)
            Padding(
              padding: const EdgeInsets.only(right: 12),
              child: _saving
                ? const SizedBox(width: 28, height: 28, child: CircularProgressIndicator(strokeWidth: 2, color: _green))
                : ElevatedButton(
                    onPressed: () => _saveFile(),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: _green.withOpacity(0.15),
                      foregroundColor: _green,
                      elevation: 0,
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
                    ),
                    child: const Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.save_rounded, size: 16),
                        SizedBox(width: 6),
                        Text('Save', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
                      ],
                    ),
                  ),
            ),
        ],
      ),
    );
  }

  Widget _buildActionButton({
    required IconData icon,
    required VoidCallback onTap,
    required String tooltip,
    bool active = false,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(4),
      child: Container(
        padding: const EdgeInsets.all(8),
        margin: const EdgeInsets.symmetric(horizontal: 2),
        child: Icon(
          icon,
          size: 18,
          color: active ? _green : _textMuted,
        ),
      ),
    );
  }

  Widget _buildSearchBar() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      decoration: BoxDecoration(
        color: _sideBarBg,
        border: Border(bottom: BorderSide(color: _border.withOpacity(0.5))),
      ),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: _searchController,
              focusNode: _searchFocusNode,
              onChanged: (value) {
                setState(() => _searchQuery = value);
                _performSearch();
              },
              style: const TextStyle(color: _textPri, fontSize: 13),
              decoration: InputDecoration(
                hintText: 'Search...',
                hintStyle: TextStyle(color: _textMuted, fontSize: 13),
                prefixIcon: const Icon(Icons.search_rounded, color: _textMuted, size: 18),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(6),
                  borderSide: BorderSide(color: _border),
                ),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(6),
                  borderSide: BorderSide(color: _border),
                ),
                focusedBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(6),
                  borderSide: const BorderSide(color: _green),
                ),
                contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                isDense: true,
              ),
            ),
          ),
          if (_matchPositions.isNotEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Text(
                '${_currentMatchIndex + 1}/${_matchPositions.length}',
                style: TextStyle(color: _textMuted, fontSize: 12, fontFamily: 'monospace'),
              ),
            ),
          IconButton(
            onPressed: _matchPositions.isEmpty ? null : _previousMatch,
            icon: const Icon(Icons.arrow_upward_rounded, size: 18),
            color: _textMuted,
            constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
            padding: EdgeInsets.zero,
          ),
          IconButton(
            onPressed: _matchPositions.isEmpty ? null : _nextMatch,
            icon: const Icon(Icons.arrow_downward_rounded, size: 18),
            color: _textMuted,
            constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
            padding: EdgeInsets.zero,
          ),
          IconButton(
            onPressed: () {
              setState(() {
                _showSearchBar = false;
                _searchQuery = '';
                _matchPositions = [];
                _searchController.clear();
              });
            },
            icon: const Icon(Icons.close_rounded, size: 18),
            color: _textMuted,
            constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
            padding: EdgeInsets.zero,
          ),
        ],
      ),
    );
  }

  Widget _buildStatusBar() {
    final lines = _textCtrl.text.split('\n').length;
    final chars = _textCtrl.text.length;
    final fileType = _getFileLanguage().toUpperCase();
    
    return Container(
      height: 28,
      padding: const EdgeInsets.symmetric(horizontal: 16),
      decoration: BoxDecoration(
        color: _statusBarBg,
        border: Border(top: BorderSide(color: _border.withOpacity(0.5))),
      ),
      child: Row(
        children: [
          // File type
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
            decoration: BoxDecoration(
              color: _cyan.withOpacity(0.15),
              borderRadius: BorderRadius.circular(4),
            ),
            child: Text(
              fileType,
              style: TextStyle(color: _cyan, fontSize: 10, fontWeight: FontWeight.w600, fontFamily: 'monospace'),
            ),
          ),
          const SizedBox(width: 16),
          
          // Line and character count
          Row(
            children: [
              Icon(Icons.library_books_rounded, color: _textMuted, size: 12),
              const SizedBox(width: 4),
              Text(
                'Ln $lines, Col ${_textCtrl.selection.baseOffset == -1 ? chars : _textCtrl.selection.baseOffset}',
                style: TextStyle(color: _textMuted, fontSize: 10, fontFamily: 'monospace'),
              ),
            ],
          ),
          const SizedBox(width: 16),
          
          // File size
          Row(
            children: [
              Icon(Icons.data_usage_rounded, color: _textMuted, size: 12),
              const SizedBox(width: 4),
              Text(
                _formatFileSize(chars),
                style: TextStyle(color: _textMuted, fontSize: 10, fontFamily: 'monospace'),
              ),
            ],
          ),
          
          const Spacer(),
          
          // Unsaved changes indicator
          if (_hasUnsavedChanges)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(
                color: _orange.withOpacity(0.15),
                borderRadius: BorderRadius.circular(4),
              ),
              child: Row(
                children: [
                  Container(
                    width: 6,
                    height: 6,
                    decoration: const BoxDecoration(color: _orange, shape: BoxShape.circle),
                  ),
                  const SizedBox(width: 6),
                  Text('Unsaved', style: TextStyle(color: _orange, fontSize: 10, fontWeight: FontWeight.w500)),
                ],
              ),
            ),
        ],
      ),
    );
  }

  String _formatFileSize(int charCount) {
    if (charCount < 1024) return '$charCount B';
    if (charCount < 1024 * 1024) return '${(charCount / 1024).toStringAsFixed(1)} KB';
    return '${(charCount / (1024 * 1024)).toStringAsFixed(1)} MB';
  }

  Widget _buildBody() {
    if (_loading) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const SizedBox(width: 32, height: 32, child: CircularProgressIndicator(strokeWidth: 2, color: _green)),
            const SizedBox(height: 16),
            Text('Loading file...', style: TextStyle(color: _textMuted, fontSize: 12)),
          ],
        ),
      );
    }
    
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24.0),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.error_outline_rounded, color: _errorRed, size: 48),
              const SizedBox(height: 16),
              Text(
                'Error loading file:\n$_error',
                textAlign: TextAlign.center,
                style: const TextStyle(color: _errorRed, fontSize: 13),
              ),
              const SizedBox(height: 24),
              ElevatedButton.icon(
                onPressed: _loadFile,
                icon: const Icon(Icons.refresh_rounded, size: 18),
                label: const Text('Retry'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: _surface,
                  foregroundColor: _textPri,
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                ),
              )
            ],
          ),
        ),
      );
    }

    return _showLineNumbers 
      ? Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Line numbers panel — scrolls in sync with editor
            SizedBox(
              width: 48,
              child: Container(
                decoration: BoxDecoration(
                  color: _editorBg,
                  border: Border(right: BorderSide(color: _border.withOpacity(0.5))),
                ),
                child: SingleChildScrollView(
                  controller: _lineNumberScrollCtrl,
                  physics: const NeverScrollableScrollPhysics(),
                  padding: const EdgeInsets.only(top: 16, bottom: 16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      for (int i = 1; i <= _textCtrl.text.split('\n').length; i++)
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                          child: Text(
                            '$i',
                            style: TextStyle(color: _lineNumber, fontSize: 12, fontFamily: 'monospace', height: 1.4),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ),
            // Editor
            Expanded(
              child: _buildEditor(),
            ),
          ],
        )
      : _buildEditor();
  }

  Widget _buildEditor() {
    return Container(
      color: _editorBg,
      child: NotificationListener<ScrollNotification>(
        onNotification: (notification) {
          // Sync line number scroll with editor scroll
          if (_lineNumberScrollCtrl.hasClients &&
              _editorScrollCtrl.hasClients &&
              notification.metrics.axis == Axis.vertical) {
            _lineNumberScrollCtrl.jumpTo(_editorScrollCtrl.offset);
          }
          return false;
        },
        child: TextField(
          controller: _textCtrl,
          focusNode: _focusNode,
          scrollController: _editorScrollCtrl,
          maxLines: null,
          expands: true,
          keyboardType: TextInputType.multiline,
          style: const TextStyle(
            color: _textPri, 
            fontFamily: 'monospace', 
            fontSize: 13,
            height: 1.4,
          ),
          decoration: const InputDecoration(
            border: InputBorder.none,
            contentPadding: EdgeInsets.all(16),
          ),
        ),
      ),
    );
  }
}