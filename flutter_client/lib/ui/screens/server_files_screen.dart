import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../api/erex_api.dart';
import '../../state/app_state.dart';
import 'file_editor_screen.dart';

const _bg        = Color(0xFF0D0F14);
const _surface   = Color(0xFF12151C);
const _card      = Color(0xFF161A22);
const _border    = Color(0xFF1F2330);
const _green     = Color(0xFF6AAF38);
const _amber     = Color(0xFFE8B84B);
const _textPri   = Colors.white;
const _textMuted = Color(0xFF5A6070);

class ServerFilesScreen extends StatefulWidget {
  const ServerFilesScreen({
    super.key,
    required this.state,
    required this.serverId,
    required this.worldName,
  });

  final AppState state;
  final String serverId;
  final String worldName;

  @override
  State<ServerFilesScreen> createState() => _ServerFilesScreenState();
}

class _ServerFilesScreenState extends State<ServerFilesScreen> {
  String _currentPath = '';
  List<FileInfo> _files = [];
  bool _loading = true;
  String? _error;
  bool _uploading = false;

  @override
  void initState() {
    super.initState();
    _loadDirectory();
  }

  Future<void> _loadDirectory() async {
    setState(() { _loading = true; _error = null; });
    try {
      final files = await widget.state.api.listFiles(widget.serverId, path: _currentPath);
      if (mounted) setState(() => _files = files);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _pickAndUpload() async {
    final result = await FilePicker.pickFiles(withData: true);
    if (result == null || result.files.isEmpty) return;

    final file = result.files.first;
    if (file.bytes == null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Cannot read file content. Please try again on a supported platform.'), backgroundColor: Colors.redAccent),
        );
      }
      return;
    }

    setState(() => _uploading = true);
    try {
      await widget.state.api.uploadFile(widget.serverId, _currentPath, file.bytes!, file.name);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Uploaded ${file.name} successfully'), backgroundColor: _green),
        );
        _loadDirectory();
      }
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Upload failed: ${e.message}'), backgroundColor: Colors.redAccent),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Upload failed: $e'), backgroundColor: Colors.redAccent),
        );
      }
    } finally {
      if (mounted) setState(() => _uploading = false);
    }
  }

  void _navigateUp() {
    if (_currentPath.isEmpty) return;
    final parts = _currentPath.split('/');
    parts.removeLast();
    _currentPath = parts.join('/');
    _loadDirectory();
  }

  void _navigateTo(String folderName) {
    if (_currentPath.isEmpty) {
      _currentPath = folderName;
    } else {
      _currentPath = '$_currentPath/$folderName';
    }
    _loadDirectory();
  }

  Future<void> _openFile(FileInfo file) async {
    final changed = await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => FileEditorScreen(
          state: widget.state,
          serverId: widget.serverId,
          path: file.path,
        ),
      ),
    );
    if (changed == true) {
      _loadDirectory();
    }
  }

  Future<void> _downloadFile(FileInfo file) async {
    try {
      final token = widget.state.api.accessToken;
      var urlStr = '${widget.state.api.baseUrl}/api/servers/${widget.serverId}/files/download?path=${Uri.encodeComponent(file.path)}';
      if (token != null) {
        urlStr += '&token=$token';
      }
      final uri = Uri.parse(urlStr);
      if (await canLaunchUrl(uri)) {
        await launchUrl(uri, mode: LaunchMode.externalApplication);
      } else {
        throw Exception('Could not launch download URL');
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Download failed: $e'), backgroundColor: Colors.redAccent),
        );
      }
    }
  }

  Future<void> _confirmDelete(FileInfo file) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: _surface,
        title: Text(
          file.isDir ? 'Delete Folder?' : 'Delete File?',
          style: const TextStyle(color: _textPri, fontWeight: FontWeight.bold, fontSize: 16),
        ),
        content: Text(
          file.isDir
              ? 'Are you sure you want to delete "${file.name}" and all its contents? This action cannot be undone.'
              : 'Are you sure you want to delete "${file.name}"? This action cannot be undone.',
          style: const TextStyle(color: _textMuted, fontSize: 13),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel', style: TextStyle(color: _textMuted)),
          ),
          ElevatedButton(
            onPressed: () => Navigator.pop(context, true),
            style: ElevatedButton.styleFrom(backgroundColor: Colors.redAccent),
            child: const Text('Delete', style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );

    if (confirmed == true) {
      _deleteItem(file);
    }
  }

  Future<void> _deleteItem(FileInfo file) async {
    setState(() { _loading = true; });
    try {
      await widget.state.api.deleteFileOrFolder(widget.serverId, file.path);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Deleted "${file.name}" successfully'),
            backgroundColor: _green,
          ),
        );
      }
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to delete: ${e.message}'),
            backgroundColor: Colors.redAccent,
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to delete: $e'),
            backgroundColor: Colors.redAccent,
          ),
        );
      }
    } finally {
      _loadDirectory();
    }
  }

  String _formatSize(int? size) {
    if (size == null) return '';
    if (size < 1024) return '$size B';
    if (size < 1024 * 1024) return '${(size / 1024).toStringAsFixed(1)} KB';
    return '${(size / (1024 * 1024)).toStringAsFixed(1)} MB';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      appBar: AppBar(
        backgroundColor: _surface,
        foregroundColor: _textPri,
        elevation: 0,
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Manage Files', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
            Text(widget.worldName, style: const TextStyle(fontSize: 11, color: _textMuted)),
          ],
        ),
        actions: [
          if (!_loading && _error == null)
            IconButton(
              icon: const Icon(Icons.refresh_rounded, size: 20),
              onPressed: _loadDirectory,
            )
        ],
      ),
      floatingActionButton: _uploading 
          ? const FloatingActionButton(onPressed: null, backgroundColor: _surface, child: CircularProgressIndicator(color: _green))
          : FloatingActionButton(
              onPressed: _pickAndUpload,
              backgroundColor: _green,
              child: const Icon(Icons.upload_file_rounded, color: Colors.white),
            ),
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _buildBreadcrumbs(),
          Expanded(child: _buildFileList()),
        ],
      ),
    );
  }

  Widget _buildBreadcrumbs() {
    final parts = _currentPath.isEmpty ? [] : _currentPath.split('/');
    
    return Container(
      color: _card,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      child: Row(
        children: [
          GestureDetector(
            onTap: () {
              if (_currentPath.isNotEmpty) {
                setState(() => _currentPath = '');
                _loadDirectory();
              }
            },
            child: Icon(Icons.home_rounded, size: 18, color: _currentPath.isEmpty ? _green : _textMuted),
          ),
          for (int i = 0; i < parts.length; i++) ...[
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 6),
              child: Icon(Icons.chevron_right_rounded, size: 16, color: _textMuted),
            ),
            GestureDetector(
              onTap: () {
                if (i < parts.length - 1) {
                  setState(() => _currentPath = parts.sublist(0, i + 1).join('/'));
                  _loadDirectory();
                }
              },
              child: Text(
                parts[i],
                style: TextStyle(
                  color: i == parts.length - 1 ? _green : _textMuted,
                  fontWeight: i == parts.length - 1 ? FontWeight.w600 : FontWeight.w400,
                  fontSize: 13,
                ),
              ),
            ),
          ]
        ],
      ),
    );
  }

  Widget _buildFileList() {
    if (_loading) return const Center(child: CircularProgressIndicator(color: _green));
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24.0),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.error_outline_rounded, color: Colors.redAccent, size: 48),
              const SizedBox(height: 16),
              Text('Error loading directory:\n$_error', textAlign: TextAlign.center, style: const TextStyle(color: Colors.redAccent)),
              const SizedBox(height: 24),
              ElevatedButton(
                onPressed: _loadDirectory,
                style: ElevatedButton.styleFrom(backgroundColor: _surface),
                child: const Text('Retry', style: TextStyle(color: _textPri)),
              )
            ],
          ),
        ),
      );
    }

    if (_files.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.folder_open_rounded, size: 48, color: _textMuted.withOpacity(0.5)),
            const SizedBox(height: 12),
            const Text('This folder is empty', style: TextStyle(color: _textMuted)),
          ],
        ),
      );
    }

    return ListView.separated(
      itemCount: _currentPath.isEmpty ? _files.length : _files.length + 1,
      separatorBuilder: (_, __) => Divider(height: 1, color: _border.withOpacity(0.5)),
      itemBuilder: (context, index) {
        if (_currentPath.isNotEmpty && index == 0) {
          return ListTile(
            leading: const Icon(Icons.folder_rounded, color: _amber),
            title: const Text('..', style: TextStyle(color: _textPri)),
            onTap: _navigateUp,
          );
        }

        final file = _files[_currentPath.isEmpty ? index : index - 1];
        return ListTile(
          leading: Icon(
            file.isDir ? Icons.folder_rounded : Icons.insert_drive_file_rounded,
            color: file.isDir ? _amber : _textMuted,
          ),
          title: Text(file.name, style: const TextStyle(color: _textPri, fontSize: 14)),
          subtitle: file.isDir
              ? null
              : Text(_formatSize(file.size), style: const TextStyle(color: _textMuted, fontSize: 11)),
          trailing: PopupMenuButton<String>(
            icon: const Icon(Icons.more_vert_rounded, color: _textMuted, size: 20),
            color: _card,
            elevation: 4,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
            onSelected: (action) {
              if (action == 'open') {
                if (file.isDir) {
                  _navigateTo(file.name);
                } else {
                  _openFile(file);
                }
              } else if (action == 'delete') {
                _confirmDelete(file);
              } else if (action == 'download') {
                _downloadFile(file);
              }
            },
            itemBuilder: (context) => [
              if (file.isDir)
                const PopupMenuItem(
                  value: 'open',
                  child: Row(
                    children: [
                      Icon(Icons.folder_open_rounded, color: _amber, size: 18),
                      SizedBox(width: 10),
                      Text('Open', style: TextStyle(color: _textPri, fontSize: 13)),
                    ],
                  ),
                )
              else
                const PopupMenuItem(
                  value: 'open',
                  child: Row(
                    children: [
                      Icon(Icons.edit_rounded, color: _green, size: 18),
                      SizedBox(width: 10),
                      Text('Edit / View', style: TextStyle(color: _textPri, fontSize: 13)),
                    ],
                  ),
                ),
              if (!file.isDir)
                const PopupMenuItem(
                  value: 'download',
                  child: Row(
                    children: [
                      Icon(Icons.download_rounded, color: Colors.blueAccent, size: 18),
                      SizedBox(width: 10),
                      Text('Download', style: TextStyle(color: _textPri, fontSize: 13)),
                    ],
                  ),
                ),
              const PopupMenuItem(
                value: 'delete',
                child: Row(
                  children: [
                    Icon(Icons.delete_outline_rounded, color: Colors.redAccent, size: 18),
                    SizedBox(width: 10),
                    Text('Delete', style: TextStyle(color: Colors.redAccent, fontSize: 13)),
                  ],
                ),
              ),
            ],
          ),
          onTap: () {
            if (file.isDir) {
              _navigateTo(file.name);
            } else {
              _openFile(file);
            }
          },
        );
      },
    );
  }
}
