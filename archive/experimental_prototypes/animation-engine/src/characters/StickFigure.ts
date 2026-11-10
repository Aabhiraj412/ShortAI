/**
 * Stick Figure Character Rig and Pose Kinematics.
 * Clean, modern 2D storytelling character with consistent proportions.
 */

export interface JointPoint {
  x: number;
  y: number;
}

export interface StickFigurePose {
  head: JointPoint;
  neck: JointPoint;
  leftShoulder: JointPoint;
  leftElbow: JointPoint;
  leftHand: JointPoint;
  rightShoulder: JointPoint;
  rightElbow: JointPoint;
  rightHand: JointPoint;
  hips: JointPoint;
  leftHip: JointPoint;
  leftKnee: JointPoint;
  leftFoot: JointPoint;
  rightHip: JointPoint;
  rightKnee: JointPoint;
  rightFoot: JointPoint;
  expression: 'neutral' | 'curious' | 'surprised' | 'happy' | 'thinking';
}

export const PROPORTIONS = {
  headRadius: 48,
  torsoLength: 140,
  upperArmLength: 85,
  forearmLength: 80,
  upperLegLength: 105,
  lowerLegLength: 100,
  lineWidth: 16
};
