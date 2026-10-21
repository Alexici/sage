import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import '../protocol/messages.dart';
import '../theme/sage_theme.dart';

class AvatarView extends StatefulWidget {
  final SageActivityState state;
  final double? level; // 0..1 reserved for audio level
  final double size;

  const AvatarView({
    super.key,
    required this.state,
    this.level,
    this.size = 180.0,
  });

  @override
  State<AvatarView> createState() => _AvatarViewState();
}

class _AvatarViewState extends State<AvatarView>
    with SingleTickerProviderStateMixin {
  late final Ticker _ticker;
  Duration? _lastElapsed;

  // Integrated continuous phase accumulators (never jump on speed changes)
  double _rotAngle = 0.0;
  double _wavePhase = 0.0;
  double _breathPhase = 0.0;
  double _auxPhase = 0.0;

  // Smoothly interpolated live parameters (calm ~2s transitions between states)
  late _SphereParams _cur;

  // Pre-generated particle lattice on the sphere (streams + ambient stardust)
  late final List<_SphereParticle> _particles;

  @override
  void initState() {
    super.initState();
    _cur = _SphereParams.forState(widget.state);
    _particles = _generateParticles();
    _ticker = createTicker(_onTick)..start();
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  void _onTick(Duration elapsed) {
    final prev = _lastElapsed;
    _lastElapsed = elapsed;
    // Clamp dt so window拖动 or tab suspension never causes a large jump
    final dt = prev == null
        ? (1.0 / 60.0)
        : ((elapsed - prev).inMicroseconds / 1e6).clamp(0.001, 0.05);

    final target = _SphereParams.forState(widget.state);

    // Calm exponential smoothing (tau = 0.75s -> ~2.2s smooth settling time)
    const double tau = 0.75;
    final double alpha = 1.0 - math.exp(-dt / tau);
    _cur = _cur.lerpTo(target, alpha);

    // Integrate phases smoothly using current interpolated velocities
    _rotAngle = (_rotAngle + _cur.rotSpeed * dt) % (200.0 * math.pi);
    _wavePhase = (_wavePhase + _cur.waveSpeed * dt) % (200.0 * math.pi);
    _breathPhase = (_breathPhase + _cur.breathSpeed * dt) % (200.0 * math.pi);
    _auxPhase = (_auxPhase + 1.4 * dt) % (200.0 * math.pi);

    setState(() {});
  }

  static List<_SphereParticle> _generateParticles() {
    final list = <_SphereParticle>[];
    final rng = math.Random(2104);

    // 14 flowing particle streams wrapping the sphere (28 particles per stream)
    const int streamCount = 14;
    const int perStream = 28;
    for (var s = 0; s < streamCount; s++) {
      // Distribute streams from pole to pole with slight organic inclination
      final v = (s + 0.5) / streamCount; // 0..1
      final baseLat = (v - 0.5) * math.pi * 0.88;
      final streamTilt = 0.22 * math.sin(s * 1.1);

      for (var p = 0; p < perStream; p++) {
        final u = p / perStream;
        final lon = u * 2.0 * math.pi + s * 0.37;
        final sizeJitter = 0.75 + rng.nextDouble() * 0.65;
        list.add(
          _SphereParticle(
            streamId: s,
            indexInStream: p,
            baseLat: baseLat,
            baseLon: lon,
            streamTilt: streamTilt,
            sizeFactor: sizeJitter,
            phaseOffset: s * 0.55 + u * 2.0 * math.pi,
            isAmbient: false,
          ),
        );
      }
    }

    // 55 floating stardust / bokeh particles drifting inside & around the rim
    for (var i = 0; i < 55; i++) {
      final u = rng.nextDouble();
      final v = rng.nextDouble();
      final theta = u * 2.0 * math.pi;
      final phi = math.acos(2.0 * v - 1.0) - (math.pi / 2.0);
      list.add(
        _SphereParticle(
          streamId: -1,
          indexInStream: i,
          baseLat: phi,
          baseLon: theta,
          streamTilt: (rng.nextDouble() - 0.5) * 0.6,
          sizeFactor: 0.6 + rng.nextDouble() * 1.15,
          phaseOffset: rng.nextDouble() * 2.0 * math.pi,
          isAmbient: true,
          radialScale: 0.82 + rng.nextDouble() * 0.28,
        ),
      );
    }

    return list;
  }

  @override
  Widget build(BuildContext context) {
    final reduceMotion = MediaQuery.disableAnimationsOf(context);
    return SizedBox(
      width: widget.size,
      height: widget.size,
      child: CustomPaint(
        painter: _ParticleSpherePainter(
          params: _cur,
          particles: _particles,
          rotAngle: reduceMotion ? 0.4 : _rotAngle,
          wavePhase: reduceMotion ? 0.0 : _wavePhase,
          breathPhase: reduceMotion ? 0.0 : _breathPhase,
          auxPhase: reduceMotion ? 0.0 : _auxPhase,
        ),
      ),
    );
  }
}

class _SphereParticle {
  final int streamId;
  final int indexInStream;
  final double baseLat;
  final double baseLon;
  final double streamTilt;
  final double sizeFactor;
  final double phaseOffset;
  final bool isAmbient;
  final double radialScale;

  const _SphereParticle({
    required this.streamId,
    required this.indexInStream,
    required this.baseLat,
    required this.baseLon,
    required this.streamTilt,
    required this.sizeFactor,
    required this.phaseOffset,
    required this.isAmbient,
    this.radialScale = 1.0,
  });
}

class _SphereParams {
  final Color colorLeft; // Cyan / electric blue hemisphere
  final Color colorCore; // Deep royal / indigo-violet center
  final Color colorRight; // Neon magenta / pink hemisphere
  final double rotSpeed; // rad/s
  final double waveSpeed; // rad/s
  final double waveAmp; // Undulation height of particle streams
  final double breathSpeed; // rad/s
  final double breathAmp; // Breathing scale amplitude
  final double baseScale; // Overall sphere scale
  final double glowIntensity; // 0..1
  final double filamentAlpha; // Network connection line visibility (0..1)
  final double listeningRing; // 0..1 smooth fade for listening ripple
  final double transcribingArc; // 0..1 smooth fade for transcribing arc

  const _SphereParams({
    required this.colorLeft,
    required this.colorCore,
    required this.colorRight,
    required this.rotSpeed,
    required this.waveSpeed,
    required this.waveAmp,
    required this.breathSpeed,
    required this.breathAmp,
    required this.baseScale,
    required this.glowIntensity,
    required this.filamentAlpha,
    required this.listeningRing,
    required this.transcribingArc,
  });

  // Reference palette from 2104.eps: Cyan-Blue -> Royal Violet -> Neon Magenta
  static const Color _cyan = Color(0xFF28C8FF);
  static const Color _brightCyan = Color(0xFF60E6FF);
  static const Color _royalViolet = Color(0xFF6A4CFF);
  static const Color _deepIndigo = Color(0xFF3B2896);
  static const Color _neonMagenta = Color(0xFFE048E8);
  static const Color _hotPink = Color(0xFFFF62D8);

  factory _SphereParams.forState(SageActivityState state) {
    switch (state) {
      case SageActivityState.offline:
        return const _SphereParams(
          colorLeft: Color(0xFF4B5668),
          colorCore: Color(0xFF2B303C),
          colorRight: Color(0xFF5A5268),
          rotSpeed: 0.04,
          waveSpeed: 0.25,
          waveAmp: 0.03,
          breathSpeed: 0.6,
          breathAmp: 0.01,
          baseScale: 0.94,
          glowIntensity: 0.18,
          filamentAlpha: 0.15,
          listeningRing: 0.0,
          transcribingArc: 0.0,
        );

      case SageActivityState.idle:
        return const _SphereParams(
          colorLeft: _cyan,
          colorCore: _royalViolet,
          colorRight: _neonMagenta,
          rotSpeed: 0.20,
          waveSpeed: 0.85,
          waveAmp: 0.09,
          breathSpeed: 1.5, // ~4.2s calm breathing cycle
          breathAmp: 0.032,
          baseScale: 1.0,
          glowIntensity: 0.58,
          filamentAlpha: 0.38,
          listeningRing: 0.0,
          transcribingArc: 0.0,
        );

      case SageActivityState.listening:
        return const _SphereParams(
          colorLeft: _brightCyan,
          colorCore: _royalViolet,
          colorRight: _neonMagenta,
          rotSpeed: 0.36,
          waveSpeed: 1.45,
          waveAmp: 0.13,
          breathSpeed: 2.2,
          breathAmp: 0.04,
          baseScale: 1.03,
          glowIntensity: 0.78,
          filamentAlpha: 0.52,
          listeningRing: 1.0,
          transcribingArc: 0.0,
        );

      case SageActivityState.transcribing:
        return const _SphereParams(
          colorLeft: _brightCyan,
          colorCore: _royalViolet,
          colorRight: _hotPink,
          rotSpeed: 0.58,
          waveSpeed: 1.9,
          waveAmp: 0.11,
          breathSpeed: 2.8,
          breathAmp: 0.03,
          baseScale: 1.01,
          glowIntensity: 0.72,
          filamentAlpha: 0.60,
          listeningRing: 0.0,
          transcribingArc: 1.0,
        );

      case SageActivityState.thinking:
      case SageActivityState.usingTool:
        return const _SphereParams(
          colorLeft: _cyan,
          colorCore: Color(0xFF8258FF),
          colorRight: _hotPink,
          rotSpeed: 0.62,
          waveSpeed: 2.2,
          waveAmp: 0.15,
          breathSpeed: 3.1,
          breathAmp: 0.042,
          baseScale: 1.02,
          glowIntensity: 0.80,
          filamentAlpha: 0.68,
          listeningRing: 0.0,
          transcribingArc: 0.0,
        );

      case SageActivityState.speaking:
        return const _SphereParams(
          colorLeft: _brightCyan,
          colorCore: _royalViolet,
          colorRight: _hotPink,
          rotSpeed: 0.48,
          waveSpeed: 3.1,
          waveAmp: 0.21,
          breathSpeed: 5.2,
          breathAmp: 0.065,
          baseScale: 1.04,
          glowIntensity: 0.95,
          filamentAlpha: 0.62,
          listeningRing: 0.0,
          transcribingArc: 0.0,
        );

      case SageActivityState.awaitingConfirm:
        return const _SphereParams(
          colorLeft: _cyan,
          colorCore: _deepIndigo,
          colorRight: SageColors.warn,
          rotSpeed: 0.14,
          waveSpeed: 0.70,
          waveAmp: 0.07,
          breathSpeed: 1.8,
          breathAmp: 0.025,
          baseScale: 1.0,
          glowIntensity: 0.70,
          filamentAlpha: 0.45,
          listeningRing: 0.0,
          transcribingArc: 0.0,
        );
    }
  }

  _SphereParams lerpTo(_SphereParams b, double t) {
    double l(double x, double y) => x + (y - x) * t;
    return _SphereParams(
      colorLeft: Color.lerp(colorLeft, b.colorLeft, t)!,
      colorCore: Color.lerp(colorCore, b.colorCore, t)!,
      colorRight: Color.lerp(colorRight, b.colorRight, t)!,
      rotSpeed: l(rotSpeed, b.rotSpeed),
      waveSpeed: l(waveSpeed, b.waveSpeed),
      waveAmp: l(waveAmp, b.waveAmp),
      breathSpeed: l(breathSpeed, b.breathSpeed),
      breathAmp: l(breathAmp, b.breathAmp),
      baseScale: l(baseScale, b.baseScale),
      glowIntensity: l(glowIntensity, b.glowIntensity),
      filamentAlpha: l(filamentAlpha, b.filamentAlpha),
      listeningRing: l(listeningRing, b.listeningRing),
      transcribingArc: l(transcribingArc, b.transcribingArc),
    );
  }
}

class _ProjectedPoint {
  final double x;
  final double y;
  final double z; // -1..1 normalized depth
  final double rim; // 0..1 Fresnel edge factor
  final Color color;
  final _SphereParticle src;

  const _ProjectedPoint({
    required this.x,
    required this.y,
    required this.z,
    required this.rim,
    required this.color,
    required this.src,
  });
}

class _ParticleSpherePainter extends CustomPainter {
  final _SphereParams params;
  final List<_SphereParticle> particles;
  final double rotAngle;
  final double wavePhase;
  final double breathPhase;
  final double auxPhase;

  const _ParticleSpherePainter({
    required this.params,
    required this.particles,
    required this.rotAngle,
    required this.wavePhase,
    required this.breathPhase,
    required this.auxPhase,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final breath =
        1.0 +
        params.breathAmp *
            (0.7 * math.sin(breathPhase) + 0.3 * math.cos(breathPhase * 1.7));
    final radius = size.shortestSide * 0.36 * params.baseScale * breath;

    // 1. Deep atmospheric outer halo (Cyan-left to Magenta-right)
    final outerGlowRect = Rect.fromCircle(
      center: center,
      radius: radius * 1.55,
    );
    final ambientHalo = Paint()
      ..shader = RadialGradient(
        colors: [
          params.colorCore.withAlpha((110 * params.glowIntensity).round()),
          params.colorLeft.withAlpha((45 * params.glowIntensity).round()),
          Colors.transparent,
        ],
        stops: const [0.0, 0.52, 1.0],
      ).createShader(outerGlowRect);
    canvas.drawCircle(center, radius * 1.55, ambientHalo);

    // 2. Volumetric 3D sphere body (subtle shaded orb core with Fresnel rim)
    final sphereRect = Rect.fromCircle(center: center, radius: radius);
    final coreShader = Paint()
      ..shader = LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [
          params.colorLeft.withAlpha((58 * params.glowIntensity).round()),
          params.colorCore.withAlpha((38 * params.glowIntensity).round()),
          params.colorRight.withAlpha((64 * params.glowIntensity).round()),
        ],
      ).createShader(sphereRect);
    canvas.drawCircle(center, radius * 0.98, coreShader);

    // Soft glowing rim highlight (matches the bright edge in 2104.eps)
    final rimPaint = Paint()
      ..shader = SweepGradient(
        colors: [
          params.colorRight.withAlpha((150 * params.glowIntensity).round()),
          params.colorCore.withAlpha((65 * params.glowIntensity).round()),
          params.colorLeft.withAlpha((165 * params.glowIntensity).round()),
          params.colorCore.withAlpha((65 * params.glowIntensity).round()),
          params.colorRight.withAlpha((150 * params.glowIntensity).round()),
        ],
        stops: const [0.0, 0.25, 0.5, 0.75, 1.0],
      ).createShader(sphereRect)
      ..style = PaintingStyle.stroke
      ..strokeWidth = math.max(1.8, radius * 0.032)
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 4.0);
    canvas.drawCircle(center, radius * 0.98, rimPaint);

    // 3. Project all 3D flowing stream particles
    final projected = <_ProjectedPoint>[];
    final streamBuckets = <int, List<_ProjectedPoint>>{};

    // Slight axial tilt so the flowing bands sweep diagonally like the reference
    const double globalTiltX = -0.32;
    const double globalTiltZ = 0.24;
    final cosTiltX = math.cos(globalTiltX);
    final sinTiltX = math.sin(globalTiltX);
    final cosTiltZ = math.cos(globalTiltZ);
    final sinTiltZ = math.sin(globalTiltZ);

    for (final p in particles) {
      // Undulating flowing wave displacement in latitude and radius
      final wave1 = math.sin(p.baseLon * 2.0 + wavePhase + p.streamId * 0.45);
      final wave2 = math.cos(p.baseLon * 3.0 - wavePhase * 0.8 + p.phaseOffset);
      final latOffset = params.waveAmp * (0.65 * wave1 + 0.35 * wave2);

      final lat = (p.baseLat + latOffset + p.streamTilt * math.sin(p.baseLon))
          .clamp(-math.pi * 0.49, math.pi * 0.49);
      final lon = p.baseLon + rotAngle * (p.isAmbient ? 0.65 : 1.0);

      final rScale = p.isAmbient
          ? p.radialScale + 0.04 * math.sin(wavePhase + p.phaseOffset)
          : 1.0 + params.waveAmp * 0.22 * wave2;

      final cosLat = math.cos(lat);
      var nx = cosLat * math.sin(lon) * rScale;
      var ny = math.sin(lat) * rScale;
      var nz = cosLat * math.cos(lon) * rScale;

      // Rotate X (axial tilt)
      final y1 = ny * cosTiltX - nz * sinTiltX;
      final z1 = ny * sinTiltX + nz * cosTiltX;
      ny = y1;
      nz = z1;

      // Rotate Z (diagonal sweep)
      final x2 = nx * cosTiltZ - ny * sinTiltZ;
      final y2 = nx * sinTiltZ + ny * cosTiltZ;
      nx = x2;
      ny = y2;

      // Perspective projection
      final persp = 1.0 + nz * 0.22;
      final sx = center.dx + nx * radius * persp;
      final sy = center.dy + ny * radius * persp;

      // Horizontal color blend: Left (-1) = Cyan, Center (0) = Violet, Right (+1) = Magenta
      final horiz = (nx.clamp(-1.0, 1.0) + 1.0) * 0.5; // 0..1
      final baseColor = horiz < 0.5
          ? Color.lerp(params.colorLeft, params.colorCore, horiz * 2.0)!
          : Color.lerp(
              params.colorCore,
              params.colorRight,
              (horiz - 0.5) * 2.0,
            )!;

      final xyDist = math.sqrt(nx * nx + ny * ny).clamp(0.0, 1.2);
      final rimFactor = math.pow(xyDist.clamp(0.0, 1.0), 1.6).toDouble();

      final pt = _ProjectedPoint(
        x: sx,
        y: sy,
        z: nz.clamp(-1.0, 1.0),
        rim: rimFactor,
        color: baseColor,
        src: p,
      );
      projected.add(pt);
      if (!p.isAmbient) {
        (streamBuckets[p.streamId] ??= []).add(pt);
      }
    }

    // 4. Draw delicate flowing network filaments along each particle stream
    if (params.filamentAlpha > 0.02) {
      final linePaint = Paint()
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round;

      for (final stream in streamBuckets.values) {
        for (var i = 0; i < stream.length; i++) {
          final a = stream[i];
          final b = stream[(i + 1) % stream.length];
          final avgZ = (a.z + b.z) * 0.5;
          if (avgZ < -0.35) continue; // Keep back-hemisphere lines subtle

          final depthFactor = ((avgZ + 1.0) * 0.5).clamp(0.15, 1.0);
          final alpha = (params.filamentAlpha * depthFactor * (45 + 75 * a.rim))
              .round()
              .clamp(0, 180);
          if (alpha <= 4) continue;

          linePaint
            ..color = a.color.withAlpha(alpha)
            ..strokeWidth = math.max(0.7, radius * 0.008 * depthFactor);
          canvas.drawLine(Offset(a.x, a.y), Offset(b.x, b.y), linePaint);
        }
      }
    }

    // 5. Sort particles back-to-front by Z depth and render glowing particle nodes
    projected.sort((a, b) => a.z.compareTo(b.z));

    final dotPaint = Paint()..style = PaintingStyle.fill;
    final bloomPaint = Paint()
      ..style = PaintingStyle.fill
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 3.5);

    final baseDotR = math.max(1.2, radius * 0.021);

    for (final pt in projected) {
      final depth01 = ((pt.z + 1.0) * 0.5).clamp(0.0, 1.0); // 0=back, 1=front
      final scale = (0.55 + 0.70 * depth01) * pt.src.sizeFactor;
      final dotR = baseDotR * scale;

      // Particles near the front and rim glow brightest (like 2104.eps)
      final brightness = (0.22 + 0.58 * depth01 + 0.35 * pt.rim).clamp(
        0.15,
        1.0,
      );
      final alpha = (255 * brightness * (0.45 + 0.55 * params.glowIntensity))
          .round()
          .clamp(18, 255);

      // Soft outer particle bloom on front-hemisphere particles
      if (pt.z > 0.05 && params.glowIntensity > 0.3) {
        bloomPaint.color = pt.color.withAlpha((alpha * 0.42).round());
        canvas.drawCircle(Offset(pt.x, pt.y), dotR * 2.1, bloomPaint);
      }

      // Crisp luminous particle core (blends toward white-lilac on hot spots)
      final coreColor = pt.z > 0.35
          ? Color.lerp(pt.color, Colors.white, 0.38 * depth01 * pt.rim)!
          : pt.color;
      dotPaint.color = coreColor.withAlpha(alpha);
      canvas.drawCircle(Offset(pt.x, pt.y), dotR, dotPaint);
    }

    // 6. Smoothly faded state overlays (Listening ripple & Transcribing arc)
    if (params.listeningRing > 0.01) {
      final rippleProg = (auxPhase % (2.0 * math.pi)) / (2.0 * math.pi);
      final rippleR = radius * (1.02 + 0.32 * rippleProg);
      final rippleAlpha = ((1.0 - rippleProg) * 160 * params.listeningRing)
          .round()
          .clamp(0, 255);
      final ringPaint = Paint()
        ..color = params.colorLeft.withAlpha(rippleAlpha)
        ..style = PaintingStyle.stroke
        ..strokeWidth = math.max(1.5, radius * 0.018);
      canvas.drawCircle(center, rippleR, ringPaint);
    }

    if (params.transcribingArc > 0.01) {
      final arcRect = Rect.fromCircle(center: center, radius: radius * 1.12);
      final arcAlpha = (210 * params.transcribingArc).round().clamp(0, 255);
      final arcPaint = Paint()
        ..color = params.colorLeft.withAlpha(arcAlpha)
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round
        ..strokeWidth = math.max(2.0, radius * 0.022);
      canvas.drawArc(arcRect, auxPhase * 1.6, math.pi * 0.65, false, arcPaint);
    }
  }

  @override
  bool shouldRepaint(covariant _ParticleSpherePainter oldDelegate) => true;
}
