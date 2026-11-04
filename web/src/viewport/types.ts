import type { Camera, EntityPatch, SceneRevision } from '../api/contracts';
import type { Surface } from './materials';

export type ViewportMode = 'orbit' | 'walk' | 'camera';
export type CameraDraft = Omit<Camera, 'id' | 'name'>;
export interface SceneViewportProps {
  scene: SceneRevision | null;
  selectedEntityId?: string;
  activeCameraId?: string;
  cameraOverride?: Camera;
  mode: ViewportMode;
  onSelectEntity: (id: string) => void;
  onSelectSurface?: (surface: Surface) => void;
  onCameraDraft: (draft: CameraDraft) => void;
  onModeChange: (mode: ViewportMode) => void;
  onEntityPatch?: (patch: EntityPatch) => void;
  readOnly: boolean;
}
