using System;
using System.Collections.Generic;
using System.Numerics;
using System.Text;

namespace AutoRouteModule.Core
{
    public class RoutePair
    {
        public Vector3 start;
        public Vector3 goal;
        public DirectionType startDirection;
        public float diameter;

        public RoutePair(Vector3 start, Vector3 goal, DirectionType startDirection, float diameter)
        {
            this.start = start;
            this.goal = goal;
            this.startDirection = startDirection;
            this.diameter = diameter;
        }
    }

    public class RouteWaypointPair : RoutePair
    {
        public List<Vector3> waypoints;
        public RouteWaypointPair(Vector3 start, Vector3 goal, DirectionType startDirection, float diameter, List<Vector3> waypoints)
            : base(start, goal, startDirection, diameter)
        {
            this.waypoints = waypoints;
        }
    }

}
