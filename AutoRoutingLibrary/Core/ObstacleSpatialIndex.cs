using System;
using System.Collections.Generic;
using System.Numerics;
using AutoRoutingLibrary.Models;

namespace AutoRoutingLibrary.Core
{
    /// <summary>
    /// 3D 공간 상의 장애물(설비, 덕트, 기존 배관 등)을 빠르게 검색하기 위한 공간 분할(Spatial Partitioning) 인덱스 클래스입니다.
    /// 전체 공간을 일정한 크기의 격자(Bucket)로 나누어, 특정 위치 주변의 장애물만 검사하게 함으로써 충돌 검사 속도를 비약적으로 향상시킵니다.
    /// </summary>
    public class ObstacleSpatialIndex
    {
        // 공간을 분할하는 한 변의 길이 (버킷의 크기)
        private readonly float _bucketSize;
        // 각 버킷(3D 좌표 인덱스)에 포함된 장애물 리스트를 저장하는 해시맵
        private readonly Dictionary<(int, int, int), List<ObstacleData>> _buckets = new();

        /// <summary>
        /// ObstacleSpatialIndex 생성자
        /// </summary>
        /// <param name="bucketSize">공간을 나눌 버킷의 한 변 길이 (기본값: 5000f). 
        /// 너무 작으면 메모리를 많이 쓰고, 너무 크면 한 버킷 내 장애물이 많아져 검색이 느려집니다.</param>
        public ObstacleSpatialIndex(float bucketSize = 5000f)
        {
            _bucketSize = bucketSize;
        }

        /// <summary>
        /// 3D 장애물 데이터를 공간 인덱스에 등록합니다.
        /// 장애물의 크기에 따라 여러 버킷에 걸쳐 등록될 수 있습니다.
        /// </summary>
        /// <param name="obstacle">등록할 장애물 데이터 (BoundingBox 포함)</param>
        public void AddObstacle(ObstacleData obstacle)
        {
            // 통과 가능한 장애물(예: 덕트 내부, 설비 내부 등)은 인덱스에 아예 등록하지 않아 충돌을 회피하지 않도록 처리
            if (obstacle.IsPassThrough()) return; 

            // 장애물이 걸쳐있는 최소/최대 버킷 좌표를 계산
            var minBucket = GetBucketCoords(obstacle.BoundingBox.Min);
            var maxBucket = GetBucketCoords(obstacle.BoundingBox.Max);

            // 해당 장애물이 포함되는 모든 버킷 큐브를 순회하며 장애물을 목록에 추가
            for (int x = minBucket.X; x <= maxBucket.X; x++)
            {
                for (int y = minBucket.Y; y <= maxBucket.Y; y++)
                {
                    for (int z = minBucket.Z; z <= maxBucket.Z; z++)
                    {
                        var key = (x, y, z);
                        if (!_buckets.TryGetValue(key, out var list))
                        {
                            list = new List<ObstacleData>();
                            _buckets[key] = list;
                        }
                        list.Add(obstacle);
                    }
                }
            }
        }

        /// <summary>
        /// 주어진 3D 좌표가 장애물 내부(충돌)에 속해 있는지 검사합니다.
        /// </summary>
        /// <param name="position">검사할 공간 좌표</param>
        /// <param name="safeMargin">배관의 두께 및 여유 공간을 의미하는 마진 (이 값만큼 장애물을 부풀려서 검사함)</param>
        /// <returns>장애물과 충돌하면 true, 아니면 false 반환</returns>
        public bool IsOccupied(Vector3 position, float safeMargin = 0f)
        {
            // 검사할 좌표가 속한 버킷 인덱스를 추출
            var bucket = GetBucketCoords(position);
            
            // 해당 버킷에 장애물이 하나라도 등록되어 있다면
            if (_buckets.TryGetValue(bucket, out var obstacles))
            {
                foreach (var obs in obstacles)
                {
                    // 원본 BoundingBox 복사
                    var inflatedBox = obs.BoundingBox;
                    
                    // 배관 두께(safeMargin)만큼 BoundingBox를 상하좌우전후로 팽창시켜 보수적으로 충돌 검사
                    if (safeMargin > 0)
                    {
                        inflatedBox.Expand(safeMargin);
                    }
                    
                    // 팽창된 장애물 영역 안에 점이 포함되면 충돌로 판정
                    if (inflatedBox.Contains(position))
                    {
                        return true;
                    }
                }
            }
            return false; // 어떤 장애물과도 충돌하지 않음
        }

        /// <summary>
        /// 실제 3D 좌표(Position)를 입력받아, 이를 Bucket(버킷) 단위의 3D 정수형 인덱스로 변환하는 헬퍼 함수
        /// </summary>
        private (int X, int Y, int Z) GetBucketCoords(Vector3 position)
        {
            return (
                (int)Math.Floor(position.X / _bucketSize),
                (int)Math.Floor(position.Y / _bucketSize),
                (int)Math.Floor(position.Z / _bucketSize)
            );
        }
    }
}
