using AutoRouteModule.Core;
using System.Collections.Generic;
using System.Numerics;

namespace AutoRouteModule.Utils
{
    public static class PathSimplifier
    {
        public static List<Int3>? SimplifyOrthogonalPath(List<Int3> path)
        {
            if (path == null || path.Count <= 2)
                return path;

            List<Int3> result = new List<Int3> { path[0] };

            Int3 previousDir = path[1] - path[0];

            for (int i = 2; i < path.Count; i++)
            {
                Int3 currentDir = path[i] - path[i - 1];

                if (!currentDir.Equals(previousDir))
                {
                    result.Add(path[i - 1]);
                    previousDir = currentDir;
                }
            }

            result.Add(path[path.Count - 1]);

            return result;
        }

        public static List<Vector3>? SimplifyOrthogonalPath(List<Vector3> path)
        {
            if (path == null || path.Count <= 2)
                return path;

            List<Vector3> result = new List<Vector3> { path[0] };

            Vector3 previousDir = path[1] - path[0];

            for (int i = 2; i < path.Count; i++)
            {
                Vector3 currentDir = path[i] - path[i - 1];

                if (!currentDir.Equals(previousDir))
                {
                    result.Add(path[i - 1]);
                    previousDir = currentDir;
                }
            }

            result.Add(path[path.Count - 1]);

            return result;
        }

        public static List<BoundBox>? SimplifyBoundBoxPath(List<BoundBox> path)
        {
            if (path == null || path.Count <= 2)
                return path;
            List<BoundBox> result = new List<BoundBox> { path[0] };
            DirectionType previousForward = path[0].Forward;
            for (int i = 1; i < path.Count; i++)
            {
                DirectionType currentForward = path[i].Forward;
                if (!currentForward.Equals(previousForward))
                {
                    result.Add(path[i - 1]);
                    previousForward = currentForward;
                }
            }
            result.Add(path[path.Count - 1]);
            return result;
        }

    }
}