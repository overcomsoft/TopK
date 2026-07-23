using System;
using System.Numerics;

namespace AutoRouteModule.Core
{
    public static class MathHelper
    {
        public static Vector3 Min(Vector3 a, Vector3 b)
        {
            return new Vector3(
                Math.Min(a.X, b.X),
                Math.Min(a.Y, b.Y),
                Math.Min(a.Z, b.Z)
            );
        }

        public static Vector3 Max(Vector3 a, Vector3 b)
        {
            return new Vector3(
                Math.Max(a.X, b.X),
                Math.Max(a.Y, b.Y),
                Math.Max(a.Z, b.Z)
            );
        }

        public static Vector3 Abs(Vector3 v)
        {
            return new Vector3(
                Math.Abs(v.X),
                Math.Abs(v.Y),
                Math.Abs(v.Z)
            );
        }

        public static float Dot(Vector3 a, Vector3 b)
        {
            return Vector3.Dot(a, b);
        }


        public static Quaternion LookRotation(Vector3 forward, Vector3 up)
        {
            forward = Vector3.Normalize(forward);

            Vector3 right = Vector3.Normalize(Vector3.Cross(up, forward));
            up = Vector3.Cross(forward, right);

            float m00 = right.X;
            float m01 = right.Y;
            float m02 = right.Z;
            float m10 = up.X;
            float m11 = up.Y;
            float m12 = up.Z;
            float m20 = forward.X;
            float m21 = forward.Y;
            float m22 = forward.Z;

            float trace = m00 + m11 + m22;
            Quaternion q = new Quaternion();

            if (trace > 0.0f)
            {
                float s = (float)Math.Sqrt(trace + 1.0f);
                q.W = s * 0.5f;
                s = 0.5f / s;
                q.X = (m12 - m21) * s;
                q.Y = (m20 - m02) * s;
                q.Z = (m01 - m10) * s;
            }
            else if (m00 >= m11 && m00 >= m22)
            {
                float s = (float)Math.Sqrt(1.0f + m00 - m11 - m22);
                q.X = 0.5f * s;
                s = 0.5f / s;
                q.Y = (m01 + m10) * s;
                q.Z = (m02 + m20) * s;
                q.W = (m12 - m21) * s;
            }
            else if (m11 > m22)
            {
                float s = (float)Math.Sqrt(1.0f + m11 - m00 - m22);
                q.Y = 0.5f * s;
                s = 0.5f / s;
                q.X = (m10 + m01) * s;
                q.Z = (m21 + m12) * s;
                q.W = (m20 - m02) * s;
            }
            else
            {
                float s = (float)Math.Sqrt(1.0f + m22 - m00 - m11);
                q.Z = 0.5f * s;
                s = 0.5f / s;
                q.X = (m20 + m02) * s;
                q.Y = (m21 + m12) * s;
                q.W = (m01 - m10) * s;
            }

            return q;
        }

        public static int Clamp(int value, int min, int max)
        {
            if (value < min) return min;
            if (value > max) return max;
            return value;
        }
    }
}
