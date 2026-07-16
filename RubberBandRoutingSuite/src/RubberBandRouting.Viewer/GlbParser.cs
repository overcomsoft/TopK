using System;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows.Media;
using System.Windows.Media.Media3D;
using RubberBandRouting.Engine;

namespace RubberBandRouting.Viewer
{
    public static class GlbParser
    {
        public static MeshGeometry3D? Parse(byte[] glbData)
        {
            if (glbData == null || glbData.Length < 20) return null;

            try
            {
                using var ms = new MemoryStream(glbData);
                using var reader = new BinaryReader(ms);

                uint magic = reader.ReadUInt32();
                if (magic != 0x46546C67) return null; // "glTF"

                uint version = reader.ReadUInt32();
                uint fileLength = reader.ReadUInt32();

                // Chunk 0: JSON
                uint jsonChunkLength = reader.ReadUInt32();
                uint jsonChunkType = reader.ReadUInt32();
                if (jsonChunkType != 0x4E4F534A) return null; // "JSON"

                byte[] jsonBytes = reader.ReadBytes((int)jsonChunkLength);
                string jsonText = Encoding.UTF8.GetString(jsonBytes);

                // Chunk 1: BIN
                byte[]? binBuffer = null;
                if (ms.Position < fileLength)
                {
                    uint binChunkLength = reader.ReadUInt32();
                    uint binChunkType = reader.ReadUInt32();
                    if (binChunkType == 0x004E4942) // "BIN"
                    {
                        binBuffer = reader.ReadBytes((int)binChunkLength);
                    }
                }

                if (binBuffer == null) return null;

                using var doc = JsonDocument.Parse(jsonText);
                var root = doc.RootElement;

                if (!root.TryGetProperty("meshes", out var meshesEl) || meshesEl.GetArrayLength() == 0) return null;
                var mesh = meshesEl[0];
                if (!mesh.TryGetProperty("primitives", out var primsEl) || primsEl.GetArrayLength() == 0) return null;
                var prim = primsEl[0];

                if (!prim.TryGetProperty("attributes", out var attrsEl) || !attrsEl.TryGetProperty("POSITION", out var posAccessorIdxEl)) return null;
                int posAccessorIdx = posAccessorIdxEl.GetInt32();

                var accessors = root.GetProperty("accessors");
                var bufferViews = root.GetProperty("bufferViews");

                // Read positions metadata
                var posAccessor = accessors[posAccessorIdx];
                int posBufferViewIdx = posAccessor.GetProperty("bufferView").GetInt32();
                int posCount = posAccessor.GetProperty("count").GetInt32();
                int posByteOffset = posAccessor.TryGetProperty("byteOffset", out var pboEl) ? pboEl.GetInt32() : 0;

                var posBufferView = bufferViews[posBufferViewIdx];
                int posBvByteOffset = posBufferView.GetProperty("byteOffset").GetInt32();

                // Parse positions
                var positions = new Point3DCollection(posCount);
                int fullPosOffset = posBvByteOffset + posByteOffset;
                for (int i = 0; i < posCount; i++)
                {
                    int offset = fullPosOffset + i * 12;
                    if (offset + 12 > binBuffer.Length) break;
                    float x = BitConverter.ToSingle(binBuffer, offset);
                    float y = BitConverter.ToSingle(binBuffer, offset + 4);
                    float z = BitConverter.ToSingle(binBuffer, offset + 8);
                    positions.Add(new Point3D(x, y, z));
                }

                // Parse indices
                var indices = new Int32Collection();
                if (prim.TryGetProperty("indices", out var idxAccessorIdxEl))
                {
                    int idxAccessorIdx = idxAccessorIdxEl.GetInt32();
                    var idxAccessor = accessors[idxAccessorIdx];
                    int idxBufferViewIdx = idxAccessor.GetProperty("bufferView").GetInt32();
                    int idxCount = idxAccessor.GetProperty("count").GetInt32();
                    int idxByteOffset = idxAccessor.TryGetProperty("byteOffset", out var iboEl) ? iboEl.GetInt32() : 0;
                    int idxComponentType = idxAccessor.GetProperty("componentType").GetInt32();

                    var idxBufferView = bufferViews[idxBufferViewIdx];
                    int idxBvByteOffset = idxBufferView.GetProperty("byteOffset").GetInt32();

                    int fullIdxOffset = idxBvByteOffset + idxByteOffset;
                    if (idxComponentType == 5123) // ushort
                    {
                        for (int i = 0; i < idxCount; i++)
                        {
                            int offset = fullIdxOffset + i * 2;
                            if (offset + 2 > binBuffer.Length) break;
                            ushort val = BitConverter.ToUInt16(binBuffer, offset);
                            indices.Add(val);
                        }
                    }
                    else if (idxComponentType == 5125) // uint
                    {
                        for (int i = 0; i < idxCount; i++)
                        {
                            int offset = fullIdxOffset + i * 4;
                            if (offset + 4 > binBuffer.Length) break;
                            uint val = BitConverter.ToUInt32(binBuffer, offset);
                            indices.Add((int)val);
                        }
                    }
                    else if (idxComponentType == 5121) // byte
                    {
                        for (int i = 0; i < idxCount; i++)
                        {
                            int offset = fullIdxOffset + i;
                            if (offset >= binBuffer.Length) break;
                            indices.Add(binBuffer[offset]);
                        }
                    }
                }
                else
                {
                    // Non-indexed: generate sequential indices
                    for (int i = 0; i < posCount; i++) indices.Add(i);
                }

                return new MeshGeometry3D
                {
                    Positions = positions,
                    TriangleIndices = indices
                };
            }
            catch
            {
                return null;
            }
        }

        public static void TransformMeshToObb(MeshGeometry3D mesh, Vec3 lbb, Vec3 rbb, Vec3 ltb, Vec3 lbf)
        {
            if (mesh.Positions == null || mesh.Positions.Count == 0) return;

            // 1. Calculate local bounding box of the parsed mesh template
            double minX = double.MaxValue, maxX = double.MinValue;
            double minY = double.MaxValue, maxY = double.MinValue;
            double minZ = double.MaxValue, maxZ = double.MinValue;

            foreach (var p in mesh.Positions)
            {
                if (p.X < minX) minX = p.X;
                if (p.X > maxX) maxX = p.X;
                if (p.Y < minY) minY = p.Y;
                if (p.Y > maxY) maxY = p.Y;
                if (p.Z < minZ) minZ = p.Z;
                if (p.Z > maxZ) maxZ = p.Z;
            }

            double sizeX = maxX - minX;
            double sizeY = maxY - minY;
            double sizeZ = maxZ - minZ;

            if (sizeX < 1e-5) sizeX = 1.0;
            if (sizeY < 1e-5) sizeY = 1.0;
            if (sizeZ < 1e-5) sizeZ = 1.0;

            // 2. Define the three orthogonal edge vectors of the target OBB
            Vec3 vX = rbb - lbb;
            Vec3 vY = ltb - lbb;
            Vec3 vZ = lbf - lbb;

            // 3. Map each local position to the oriented bounding box in world space
            var worldPositions = new Point3DCollection(mesh.Positions.Count);
            foreach (var p in mesh.Positions)
            {
                // Normalize to [0, 1] range inside local bounding box
                double nx = (p.X - minX) / sizeX;
                double ny = (p.Y - minY) / sizeY;
                double nz = (p.Z - minZ) / sizeZ;

                // Scale, rotate and translate to OBB coordinates in world space
                double wx = lbb.X + nx * vX.X + ny * vY.X + nz * vZ.X;
                double wy = lbb.Y + nx * vX.Y + ny * vY.Y + nz * vZ.Y;
                double wz = lbb.Z + nx * vX.Z + ny * vY.Z + nz * vZ.Z;

                worldPositions.Add(new Point3D(wx, wy, wz));
            }

            mesh.Positions = worldPositions;
        }
    }
}
