using System;
using System.Numerics;

namespace AutoRoutingLibrary.Models
{
    /// <summary>
    /// 3D 공간 상의 직육면체(Axis-Aligned Bounding Box) 영역을 나타내는 구조체입니다.
    /// 장애물이나 설비가 차지하는 물리적 공간을 수학적으로 빠르고 간단하게 표현합니다.
    /// </summary>
    public struct BoundingBox3D
    {
        // 박스의 최소 좌표 (가장 작고 낮은 구석의 3D 좌표)
        public Vector3 Min { get; set; }
        // 박스의 최대 좌표 (가장 크고 높은 구석의 3D 좌표)
        public Vector3 Max { get; set; }

        /// <summary>
        /// BoundingBox3D 생성자
        /// 입력된 두 점을 비교하여 정확한 Min/Max 좌표로 정렬합니다.
        /// </summary>
        public BoundingBox3D(Vector3 min, Vector3 max)
        {
            Min = new Vector3(Math.Min(min.X, max.X), Math.Min(min.Y, max.Y), Math.Min(min.Z, max.Z));
            Max = new Vector3(Math.Max(min.X, max.X), Math.Max(min.Y, max.Y), Math.Max(min.Z, max.Z));
        }

        /// <summary>
        /// 다른 BoundingBox3D와 공간상에서 겹치는지(충돌하는지) 여부를 확인합니다.
        /// (AABB 간의 빠른 충돌 검사 알고리즘 적용)
        /// </summary>
        public bool Intersects(BoundingBox3D other)
        {
            return (Min.X <= other.Max.X && Max.X >= other.Min.X) &&
                   (Min.Y <= other.Max.Y && Max.Y >= other.Min.Y) &&
                   (Min.Z <= other.Max.Z && Max.Z >= other.Min.Z);
        }

        /// <summary>
        /// 3D 공간 상의 특정 점(point)이 이 직육면체 내부에 완전히 포함되어 있는지 검사합니다.
        /// </summary>
        public bool Contains(Vector3 point)
        {
            return (point.X >= Min.X && point.X <= Max.X) &&
                   (point.Y >= Min.Y && point.Y <= Max.Y) &&
                   (point.Z >= Min.Z && point.Z <= Max.Z);
        }
        
        /// <summary>
        /// 배관의 두께나 추가 여유 공간(Margin)을 반영하기 위해 직육면체의 크기를 전 방향으로 부풀립니다.
        /// </summary>
        public void Expand(float margin)
        {
            Min = new Vector3(Min.X - margin, Min.Y - margin, Min.Z - margin);
            Max = new Vector3(Max.X + margin, Max.Y + margin, Max.Z + margin);
        }
    }
}
