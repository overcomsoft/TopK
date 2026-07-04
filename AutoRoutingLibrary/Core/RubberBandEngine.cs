using System;
using System.Collections.Generic;
using System.Text;

namespace AutoRoutingLibrary.Core
{
    public sealed class RubberBandStep
    {
        public int StepIndex { get; set; }
        public string StepDescription { get; set; } = string.Empty;
        public List<Vec3> RubberBandWaypoints { get; } = new();
        public List<Vec3> CollisionPoints { get; } = new();
    }

    public sealed class RubberBandEngine : IDisposable
    {
        private readonly IntPtr _handle;
        private bool _disposed;

        public RubberBandEngine()
        {
            _handle = Native.r3d_rubber_create();
            if (_handle == IntPtr.Zero)
            {
                throw new InvalidOperationException("Failed to create native RubberBandEngine instance.");
            }
        }

        public void Initialize(
            int maxVerticalBends,
            double safetyMargin,
            double trayWidth,
            double trayHeight,
            double pipePitch,
            int pipeCount,
            double[] freqZLevels,
            List<Aabb> freqBendZones)
        {
            CheckDisposed();
            var cfg = new Native.R3dRubberConfig
            {
                max_vertical_bends = maxVerticalBends,
                safety_margin = safetyMargin,
                tray_width = trayWidth,
                tray_height = trayHeight,
                pipe_pitch = pipePitch,
                pipe_count = pipeCount
            };

            Native.R3dAABB[]? nativeZones = null;
            if (freqBendZones != null && freqBendZones.Count > 0)
            {
                nativeZones = new Native.R3dAABB[freqBendZones.Count];
                for (int i = 0; i < freqBendZones.Count; i++)
                {
                    nativeZones[i] = new Native.R3dAABB
                    {
                        min_x = freqBendZones[i].Min.X,
                        min_y = freqBendZones[i].Min.Y,
                        min_z = freqBendZones[i].Min.Z,
                        max_x = freqBendZones[i].Max.X,
                        max_y = freqBendZones[i].Max.Y,
                        max_z = freqBendZones[i].Max.Z
                    };
                }
            }

            int status = Native.r3d_rubber_initialize(
                _handle,
                cfg,
                freqZLevels,
                freqZLevels?.Length ?? 0,
                nativeZones,
                nativeZones?.Length ?? 0
            );

            if (status != 0)
            {
                throw new Exception($"r3d_rubber_initialize failed with status {status}");
            }
        }

        public void IngestObstacles(List<Aabb> obstacles)
        {
            CheckDisposed();
            Native.R3dAABB[]? nativeObs = null;
            if (obstacles != null && obstacles.Count > 0)
            {
                nativeObs = new Native.R3dAABB[obstacles.Count];
                for (int i = 0; i < obstacles.Count; i++)
                {
                    nativeObs[i] = new Native.R3dAABB
                    {
                        min_x = obstacles[i].Min.X,
                        min_y = obstacles[i].Min.Y,
                        min_z = obstacles[i].Min.Z,
                        max_x = obstacles[i].Max.X,
                        max_y = obstacles[i].Max.Y,
                        max_z = obstacles[i].Max.Z
                    };
                }
            }

            int status = Native.r3d_rubber_ingest_obstacles(
                _handle,
                nativeObs,
                nativeObs?.Length ?? 0
            );

            if (status != 0)
            {
                throw new Exception($"r3d_rubber_ingest_obstacles failed with status {status}");
            }
        }

        public void Execute(Vec3 start, Vec3 end)
        {
            CheckDisposed();
            var s = new Native.R3dPoint3D { x = start.X, y = start.Y, z = start.Z };
            var e = new Native.R3dPoint3D { x = end.X, y = end.Y, z = end.Z };

            int status = Native.r3d_rubber_execute(_handle, s, e);
            if (status != 0)
            {
                throw new Exception($"r3d_rubber_execute failed with status {status}");
            }
        }

        public int GetStepCount()
        {
            CheckDisposed();
            return Native.r3d_rubber_get_step_count(_handle);
        }

        public RubberBandStep GetStepDetails(int stepIndex)
        {
            CheckDisposed();
            var descBuffer = new byte[2048];
            var wpBuffer = new Native.R3dPoint3D[2000];
            var colBuffer = new Native.R3dPoint3D[200];

            int status = Native.r3d_rubber_get_step_details(
                _handle,
                stepIndex,
                descBuffer,
                descBuffer.Length,
                wpBuffer,
                wpBuffer.Length,
                out int wpCount,
                colBuffer,
                colBuffer.Length,
                out int colCount
            );

            if (status != 0)
            {
                throw new Exception($"r3d_rubber_get_step_details failed with status {status}");
            }

            // Parse description
            int nullIdx = Array.IndexOf(descBuffer, (byte)0);
            string desc = Encoding.UTF8.GetString(descBuffer, 0, nullIdx >= 0 ? nullIdx : descBuffer.Length);

            var step = new RubberBandStep
            {
                StepIndex = stepIndex + 1,
                StepDescription = desc
            };

            for (int i = 0; i < wpCount; i++)
            {
                step.RubberBandWaypoints.Add(new Vec3(wpBuffer[i].x, wpBuffer[i].y, wpBuffer[i].z));
            }

            for (int i = 0; i < colCount; i++)
            {
                step.CollisionPoints.Add(new Vec3(colBuffer[i].x, colBuffer[i].y, colBuffer[i].z));
            }

            return step;
        }

        public List<Vec3> GetPipePath(int pipeIndex)
        {
            CheckDisposed();
            var ptBuffer = new Native.R3dPoint3D[2000];
            int count = Native.r3d_rubber_get_pipe_path(_handle, pipeIndex, ptBuffer, ptBuffer.Length);
            
            var list = new List<Vec3>(count);
            for (int i = 0; i < count; i++)
            {
                list.Add(new Vec3(ptBuffer[i].x, ptBuffer[i].y, ptBuffer[i].z));
            }
            return list;
        }

        private void CheckDisposed()
        {
            if (_disposed) throw new ObjectDisposedException(nameof(RubberBandEngine));
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            Native.r3d_rubber_destroy(_handle);
        }
    }
}
