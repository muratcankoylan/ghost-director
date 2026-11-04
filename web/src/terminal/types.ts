import type { Camera, SceneRevision } from '../api/contracts';

export type CameraDraft = Omit<Camera, 'id' | 'name'>;
export type SurfaceName = 'walls' | 'ceiling' | 'floor' | 'trim' | 'curtains';
export type DesignTool = 'scene' | 'materials' | 'lighting' | 'camera';

export interface TerminalViewportProps {
  scene: SceneRevision | null;
  selectedEntityId?: string;
  activeCameraId?: string;
  cameraOverride?: Camera;
  mode: 'orbit' | 'walk' | 'camera';
  onSelectEntity: (id: string) => void;
  onSelectSurface?: (surface: SurfaceName) => void;
  onCameraDraft: (draft: CameraDraft) => void;
  onModeChange: (mode: 'orbit' | 'walk' | 'camera') => void;
  readOnly: boolean;
}
