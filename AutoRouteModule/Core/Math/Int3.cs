using System;
using System.Numerics;
using System.Runtime.InteropServices;

namespace AutoRouteModule.Core
{
    [StructLayout(LayoutKind.Sequential)]
    public struct Int3 : IEquatable<Int3>
    {
        public int x;
        public int y;
        public int z;

        public Int3(int x, int y, int z)
        {
            this.x = x;
            this.y = y;
            this.z = z;
        }

        public static Int3 Zero => new Int3(0, 0, 0);
        public static Int3 One => new Int3(1, 1, 1);

        public static Int3 operator +(Int3 a, Int3 b)
        {
            return new Int3(a.x + b.x, a.y + b.y, a.z + b.z);
        }

        public static Int3 operator -(Int3 a, Int3 b)
        {
            return new Int3(a.x - b.x, a.y - b.y, a.z - b.z);
        }

        public static Int3 operator -(Int3 a)
        {
            return new Int3(-a.x, -a.y, -a.z);
        }

        public static Int3 operator *(Int3 a, int scalar)
        {
            return new Int3(a.x * scalar, a.y * scalar, a.z * scalar);
        }

        public static Int3 operator /(Int3 a, int scalar)
        {
            return new Int3(a.x / scalar, a.y / scalar, a.z / scalar);
        }

        public static bool operator ==(Int3 a, Int3 b)
        {
            return a.x == b.x && a.y == b.y && a.z == b.z;
        }

        public static bool operator !=(Int3 a, Int3 b)
        {
            return !(a == b);
        }

        public bool Equals(Int3 other)
        {
            return x == other.x && y == other.y && z == other.z;
        }

        public override bool Equals(object obj)
        {
            return obj is Int3 other && Equals(other);
        }

        public override int GetHashCode()
        {
            unchecked
            {
                int hash = 17;
                hash = hash * 31 + x;
                hash = hash * 31 + y;
                hash = hash * 31 + z;
                return hash;
            }
        }

        public override string ToString()
        {
            return $"Int3({x}, {y}, {z})";
        }

        public static Int3 Min(Int3 a, Int3 b)
        {
            return new Int3(
                Math.Min(a.x, b.x),
                Math.Min(a.y, b.y),
                Math.Min(a.z, b.z)
            );
        }

        public static Int3 Max(Int3 a, Int3 b)
        {
            return new Int3(
                Math.Max(a.x, b.x),
                Math.Max(a.y, b.y),
                Math.Max(a.z, b.z)
            );
        }

        public static Int3 Clamp(Int3 value, int min, int max)
        {
            return new Int3(
                MathHelper.Clamp(value.x, min, max),
                MathHelper.Clamp(value.y, min, max),
                MathHelper.Clamp(value.z, min, max)
            );
        }

        public Vector3 ToVector3()
        {
            return new Vector3(x, y, z);
        }
    }
}
