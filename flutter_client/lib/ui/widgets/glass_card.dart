import 'dart:ui';
import 'package:flutter/material.dart';
import '../theme/tranquil_theme.dart';
import '../../services/audio_service.dart';

class GlassCard extends StatelessWidget {
  const GlassCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(16),
    this.margin = EdgeInsets.zero,
    this.isSelected = false,
    this.onTap,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final EdgeInsetsGeometry margin;
  final bool isSelected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    Widget card = ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: TranquilTheme.blurSigma, sigmaY: TranquilTheme.blurSigma),
        child: Container(
          padding: padding,
          decoration: TranquilTheme.glassDecoration(isSelected: isSelected),
          child: Stack(
            clipBehavior: Clip.none,
            children: [
              child,
              if (isSelected) ...[
                Positioned(top: -4, left: -4, child: _buildCornerDot()),
                Positioned(top: -4, right: -4, child: _buildCornerDot()),
                Positioned(bottom: -4, left: -4, child: _buildCornerDot()),
                Positioned(bottom: -4, right: -4, child: _buildCornerDot()),
              ]
            ],
          ),
        ),
      ),
    );

    if (onTap != null) {
      card = GestureDetector(
        onTap: () {
          AudioService.playClick();
          onTap!();
        },
        behavior: HitTestBehavior.opaque,
        child: card,
      );
    }

    if (margin != EdgeInsets.zero) {
      card = Padding(padding: margin, child: card);
    }

    return card;
  }

  Widget _buildCornerDot() {
    return Container(
      width: 4,
      height: 4,
      decoration: BoxDecoration(
        color: TranquilTheme.glowCyan,
        shape: BoxShape.circle,
        boxShadow: [
          BoxShadow(color: TranquilTheme.glowCyan, blurRadius: 4, spreadRadius: 1)
        ],
      ),
    );
  }
}
