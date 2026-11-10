import { StickFigurePose, PROPORTIONS } from './StickFigure';

export function getPose(
  poseType: 'standing' | 'thinking' | 'pointing' | 'presenting' | 'surprised' | 'walking' | 'falling',
  baseX: number,
  baseY: number,
  t: number
): StickFigurePose {
  const breathY = Math.sin(t * 3.0) * 6;
  const hipY = baseY + breathY;
  const neckY = hipY - PROPORTIONS.torsoLength;
  const headY = neckY - PROPORTIONS.headRadius * 1.3;

  switch (poseType) {
    case 'thinking':
      return {
        head: { x: baseX, y: headY },
        neck: { x: baseX, y: neckY },
        hips: { x: baseX, y: hipY },
        leftShoulder: { x: baseX - 30, y: neckY + 15 },
        leftElbow: { x: baseX - 55, y: neckY + 80 },
        leftHand: { x: baseX - 45, y: neckY + 140 },
        rightShoulder: { x: baseX + 30, y: neckY + 15 },
        rightElbow: { x: baseX + 65, y: neckY + 70 },
        rightHand: { x: baseX + 25, y: headY + 30 }, // Hand on chin
        leftHip: { x: baseX - 25, y: hipY },
        leftKnee: { x: baseX - 35, y: hipY + PROPORTIONS.upperLegLength },
        leftFoot: { x: baseX - 40, y: hipY + PROPORTIONS.upperLegLength + PROPORTIONS.lowerLegLength },
        rightHip: { x: baseX + 25, y: hipY },
        rightKnee: { x: baseX + 35, y: hipY + PROPORTIONS.upperLegLength },
        rightFoot: { x: baseX + 40, y: hipY + PROPORTIONS.upperLegLength + PROPORTIONS.lowerLegLength },
        expression: 'thinking'
      };

    case 'pointing':
      return {
        head: { x: baseX, y: headY },
        neck: { x: baseX, y: neckY },
        hips: { x: baseX, y: hipY },
        leftShoulder: { x: baseX - 30, y: neckY + 15 },
        leftElbow: { x: baseX - 60, y: neckY + 70 },
        leftHand: { x: baseX - 50, y: neckY + 140 },
        rightShoulder: { x: baseX + 30, y: neckY + 15 },
        rightElbow: { x: baseX + 100, y: neckY - 20 },
        rightHand: { x: baseX + 180, y: neckY - 80 }, // Pointing high right
        leftHip: { x: baseX - 25, y: hipY },
        leftKnee: { x: baseX - 35, y: hipY + PROPORTIONS.upperLegLength },
        leftFoot: { x: baseX - 40, y: hipY + PROPORTIONS.upperLegLength + PROPORTIONS.lowerLegLength },
        rightHip: { x: baseX + 25, y: hipY },
        rightKnee: { x: baseX + 35, y: hipY + PROPORTIONS.upperLegLength },
        rightFoot: { x: baseX + 40, y: hipY + PROPORTIONS.upperLegLength + PROPORTIONS.lowerLegLength },
        expression: 'curious'
      };

    case 'presenting':
      return {
        head: { x: baseX, y: headY },
        neck: { x: baseX, y: neckY },
        hips: { x: baseX, y: hipY },
        leftShoulder: { x: baseX - 30, y: neckY + 15 },
        leftElbow: { x: baseX - 90, y: neckY + 40 },
        leftHand: { x: baseX - 150, y: neckY }, // Open left
        rightShoulder: { x: baseX + 30, y: neckY + 15 },
        rightElbow: { x: baseX + 90, y: neckY + 40 },
        rightHand: { x: baseX + 150, y: neckY }, // Open right
        leftHip: { x: baseX - 25, y: hipY },
        leftKnee: { x: baseX - 35, y: hipY + PROPORTIONS.upperLegLength },
        leftFoot: { x: baseX - 40, y: hipY + PROPORTIONS.upperLegLength + PROPORTIONS.lowerLegLength },
        rightHip: { x: baseX + 25, y: hipY },
        rightKnee: { x: baseX + 35, y: hipY + PROPORTIONS.upperLegLength },
        rightFoot: { x: baseX + 40, y: hipY + PROPORTIONS.upperLegLength + PROPORTIONS.lowerLegLength },
        expression: 'happy'
      };

    case 'falling':
      const tumble = Math.sin(t * 5.0) * 15;
      return {
        head: { x: baseX + tumble, y: headY - 20 },
        neck: { x: baseX, y: neckY },
        hips: { x: baseX, y: hipY },
        leftShoulder: { x: baseX - 30, y: neckY + 15 },
        leftElbow: { x: baseX - 80, y: neckY - 60 },
        leftHand: { x: baseX - 120, y: neckY - 140 }, // Flailing up
        rightShoulder: { x: baseX + 30, y: neckY + 15 },
        rightElbow: { x: baseX + 80, y: neckY - 60 },
        rightHand: { x: baseX + 130, y: neckY - 150 }, // Flailing up
        leftHip: { x: baseX - 25, y: hipY },
        leftKnee: { x: baseX - 70, y: hipY + 60 },
        leftFoot: { x: baseX - 100, y: hipY + 140 },
        rightHip: { x: baseX + 25, y: hipY },
        rightKnee: { x: baseX + 70, y: hipY + 60 },
        rightFoot: { x: baseX + 110, y: hipY + 130 },
        expression: 'surprised'
      };

    default: // standing
      return {
        head: { x: baseX, y: headY },
        neck: { x: baseX, y: neckY },
        hips: { x: baseX, y: hipY },
        leftShoulder: { x: baseX - 30, y: neckY + 15 },
        leftElbow: { x: baseX - 50, y: neckY + 80 },
        leftHand: { x: baseX - 45, y: neckY + 145 },
        rightShoulder: { x: baseX + 30, y: neckY + 15 },
        rightElbow: { x: baseX + 50, y: neckY + 80 },
        rightHand: { x: baseX + 45, y: neckY + 145 },
        leftHip: { x: baseX - 25, y: hipY },
        leftKnee: { x: baseX - 30, y: hipY + PROPORTIONS.upperLegLength },
        leftFoot: { x: baseX - 35, y: hipY + PROPORTIONS.upperLegLength + PROPORTIONS.lowerLegLength },
        rightHip: { x: baseX + 25, y: hipY },
        rightKnee: { x: baseX + 30, y: hipY + PROPORTIONS.upperLegLength },
        rightFoot: { x: baseX + 35, y: hipY + PROPORTIONS.upperLegLength + PROPORTIONS.lowerLegLength },
        expression: 'neutral'
      };
  }
}
