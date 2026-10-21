import 'dart:ui';

import 'package:flutter/material.dart';

import '../../theme/sage_theme.dart';

class FrostedBox extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry? padding;
  final double borderRadius;
  final Color? borderColor;
  final Color? tintColor;
  final int alpha;
  final double blurSigma;

  const FrostedBox({
    super.key,
    required this.child,
    this.padding,
    this.borderRadius = SageTheme.radius,
    this.borderColor,
    this.tintColor,
    this.alpha = 165, // ~65% opacity for real glass translucency
    this.blurSigma = 16.0,
  });

  @override
  Widget build(BuildContext context) {
    final bg = (tintColor ?? SageColors.surface).withAlpha(alpha);
    final border = borderColor ?? SageColors.border.withAlpha(200);

    return ClipRRect(
      borderRadius: BorderRadius.circular(borderRadius),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: blurSigma, sigmaY: blurSigma),
        child: Container(
          padding: padding,
          decoration: BoxDecoration(
            color: bg,
            borderRadius: BorderRadius.circular(borderRadius),
            border: Border.all(color: border, width: 1.0),
          ),
          child: child,
        ),
      ),
    );
  }
}

class FrostedCircleButton extends StatelessWidget {
  final Widget child;
  final double size;
  final VoidCallback? onTap;
  final String? tooltip;
  final Color? borderColor;
  final Color? tintColor;
  final int alpha;

  const FrostedCircleButton({
    super.key,
    required this.child,
    this.size = 42.0,
    this.onTap,
    this.tooltip,
    this.borderColor,
    this.tintColor,
    this.alpha = 155,
  });

  @override
  Widget build(BuildContext context) {
    Widget btn = GestureDetector(
      onTap: onTap,
      child: MouseRegion(
        cursor: onTap != null
            ? SystemMouseCursors.click
            : SystemMouseCursors.basic,
        child: SizedBox(
          width: size,
          height: size,
          child: FrostedBox(
            borderRadius: size / 2,
            borderColor: borderColor,
            tintColor: tintColor,
            alpha: alpha,
            child: Center(child: child),
          ),
        ),
      ),
    );
    if (tooltip != null && tooltip!.isNotEmpty) {
      btn = Tooltip(message: tooltip!, child: btn);
    }
    return btn;
  }
}
