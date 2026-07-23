

using System;
using System.Numerics;

namespace AutoRouteModule.Core
{
    public readonly struct BoundBox : IEquatable<BoundBox>
    {
        public readonly AABB Bound;
        public readonly DirectionType Forward;
        public readonly DirectionType Up;

        public BoundBox(AABB aabb, DirectionType forward, DirectionType up)
        {
            Bound = aabb;
            Forward = forward;
            Up = up;
        }

        // AABB와 진행 방향이 모두 같으면 같은 탐색 상태로 취급합니다.
        public bool Equals(BoundBox other)
        {
            return Bound.Equals(other.Bound) && Forward.Equals(other.Forward) && Up.Equals(other.Up);
        }

        public override bool Equals(object obj)
        {
            return obj is BoundBox other && Equals(other);
        }

        public override int GetHashCode()
        {
            unchecked
            {
                int hash = 17;
                hash = hash * 31 + Bound.GetHashCode();
                hash = hash * 31 + Forward.GetHashCode();
                hash = hash * 31 + Up.GetHashCode();
                return hash;
            }
        }

        public BoundBox MoveForward()
        {
            Vector3 center = Bound.Center + Bound.Size * Directions.GetDirection(Forward).ToVector3();
            AABB nextBound = AABB.FromCenterSize(center, Bound.Size);
            return new BoundBox(nextBound, Forward, Up);
        }

        public BoundBox MoveForwardDistance(float distance)
        {
            Vector3 center = Bound.Center + Directions.GetDirection(Forward).ToVector3() * distance;
            AABB nextBound = AABB.FromCenterSize(center, Bound.Size);
            return new BoundBox(nextBound, Forward, Up);
        }

        public BoundBox Rotate(DirectionType nextDirection)
        {
            if (Forward == nextDirection)
                return MoveForward();


            DirectionType nextUp = GetNextUpDirection(nextDirection);
            Vector3 nextSize = GetNextSize(nextDirection, nextUp);


            float halfForward = GetSizeByDirection(Forward) * 0.5f;
            float halfNext = GetSizeByDirection(nextDirection) * 0.5f;
            Vector3 forwardDistance = Directions.GetDirection(Forward).ToVector3() * (halfNext - halfForward);
            Vector3 nextDistance = Directions.GetDirection(nextDirection).ToVector3() * (halfNext + halfForward);
            Vector3 nextCenter = Bound.Center + forwardDistance + nextDistance;



            AABB nextBound = AABB.FromCenterSize(nextCenter, nextSize);

            return new BoundBox(nextBound, nextDirection, nextUp);
        }

        public Vector3 GetNextSize(DirectionType nextFoward, DirectionType nextUp)
        {
            if (Forward == nextFoward)
                return Bound.Size;

            Quaternion baseRotation = Quaternion.CreateFromRotationMatrix(Matrix4x4.CreateLookAt(Vector3.Zero,Forward.ToVector3(), Up.ToVector3()));
            Quaternion targetRotation = Quaternion.CreateFromRotationMatrix(Matrix4x4.CreateLookAt(Vector3.Zero, nextFoward.ToVector3(), nextUp.ToVector3()));

            Quaternion rotation = Quaternion.Inverse(baseRotation) * targetRotation;
            return Vector3.Abs( Vector3.Transform(Bound.Size, rotation));
        }

        public DirectionType GetNextUpDirection(DirectionType nextDirection)
        {
            if (nextDirection == Up)
                return Directions.GetOppositeDirection(Forward);
            else if(nextDirection == Directions.GetOppositeDirection(Up))
                return Forward;


            return Up;

        }


        public float GetSizeByDirection(DirectionType direction)
        {
            switch (direction)
            {
                case DirectionType.Right:
                case DirectionType.Left:
                    return Bound.Size.X;
                case DirectionType.Up:
                case DirectionType.Down:
                    return Bound.Size.Y;
                case DirectionType.Forward:
                case DirectionType.Backward:
                    return Bound.Size.Z;
                default:
                    throw new ArgumentException($"Invalid direction: {direction}");
            }
        }

        public static BoundBox CreateNextBox(in BoundBox current, DirectionType nextDirection)
        {
            if (current.Forward == nextDirection)
            {
                return current.MoveForward();
            }
            else
            {
                return current.Rotate(nextDirection);
            }
        }




    }
}
