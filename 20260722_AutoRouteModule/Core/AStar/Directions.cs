
using System;
using System.Numerics;

namespace AutoRouteModule.Core
{
    public enum DirectionType : ushort
    {
        Right = 0,
        Left = 1,
        Up = 2,
        Down = 3,
        Forward = 4,
        Backward = 5,

        None,
    }


    public static class Directions
    {
        
        public static readonly Int3[] Direction =
        {
            new Int3( 1, 0, 0),
            new Int3(-1, 0, 0),
            new Int3( 0, 1, 0),
            new Int3( 0,-1, 0),
            new Int3( 0, 0, 1),
            new Int3( 0, 0,-1),
        };

        

        public static Int3 Right => Direction[(int)DirectionType.Right];
        public static Int3 Left => Direction[(int)DirectionType.Left];
        public static Int3 Up => Direction[(int)DirectionType.Up];
        public static Int3 Down => Direction[(int)DirectionType.Down];
        public static Int3 Forward => Direction[(int)DirectionType.Forward];
        public static Int3 Backward => Direction[(int)DirectionType.Backward];

        public static int Length = 6;

        public static Int3 GetDirection(DirectionType direction)
        {
            if (direction == DirectionType.None)
            {
                return Int3.Zero;
            }

            return Direction[(int)direction];
        }

        public static DirectionType GetClosestDirection(Vector3 v)
        {
            return GetClosestDirection(v.X, v.Y, v.Z);
        }

        public static DirectionType GetClosestDirection(float x, float y, float z)
        {
            if (x * x + y * y + z * z < float.Epsilon)
                return DirectionType.None;

            float ax = Math.Abs(x);
            float ay = Math.Abs(y);
            float az = Math.Abs(z);

            if (ax >= ay && ax >= az)
                return x >= 0 ? DirectionType.Right : DirectionType.Left;

            if (ay >= az)
                return y >= 0 ? DirectionType.Up : DirectionType.Down;

            return z >= 0 ? DirectionType.Forward : DirectionType.Backward;
        }

        public static DirectionType GetOppositeDirection(DirectionType direction)
        {
            switch (direction)
            {
                case DirectionType.Right: return DirectionType.Left;
                case DirectionType.Left: return DirectionType.Right;
                case DirectionType.Up: return DirectionType.Down;
                case DirectionType.Down: return DirectionType.Up;
                case DirectionType.Forward: return DirectionType.Backward;
                case DirectionType.Backward: return DirectionType.Forward;
                default: return DirectionType.None;
            }
        }

        public static Vector3 ToVector3(this DirectionType direction)
        {
            switch (direction)
            {
                case DirectionType.Right: return new Vector3(1, 0, 0);
                case DirectionType.Left: return new Vector3(-1, 0, 0);
                case DirectionType.Up: return new Vector3(0, 1, 0);
                case DirectionType.Down: return new Vector3(0, -1, 0);
                case DirectionType.Forward: return new Vector3(0, 0, 1);
                case DirectionType.Backward: return new Vector3(0, 0, -1);
                default: return Vector3.Zero;
            }
        }

    }
}

