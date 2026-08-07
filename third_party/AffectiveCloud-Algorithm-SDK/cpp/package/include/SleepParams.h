#ifndef AA86A1EC_6415_4A9B_A963_5DBEA640D102
#define AA86A1EC_6415_4A9B_A963_5DBEA640D102

namespace basic
{
    namespace affection
    {
        namespace params
        {

            std::vector<std::vector<double>> betaDegreeList = {
                {0, 0.1, 0.2, 0.28, 0.4, 0.48, 0.7, 1},
                {0, 0, 30, 50, 80, 90, 100, 100}};

            std::vector<std::vector<double>> betaWeightList = {
                {0, 0.3, 0.4, 1},
                {0.1, 0.1, 0.4, 0.4}};

            std::vector<std::vector<double>> thetaDegreeList = {
                {0, 0.1, 0.2, 0.25, 0.3, 0.5, 1},
                {100, 100, 80, 55, 30, 0, 0}};

            std::vector<std::vector<double>> thetaWeightList = {
                {0, 0.15, 0.22, 1},
                {0.1, 0.1, 0.3, 0.3}};

            std::vector<std::vector<double>> deltaDegreeList = {
                {0, 0.02, 0.08, 0.1, 0.12, 0.15, 0.2, 1},
                {100, 100, 80, 70, 40, 20, 0, 0}};

            std::vector<std::vector<double>> deltaWeightList = {
                {0, 0.12, 0.15, 1},
                {0.1, 0.1, 0.2, 0.2}};

            std::vector<std::vector<double>> gammaDegreeList = {
                {0, 0.01, 0.06, 0.12, 0.2, 0.4, 1},
                {0, 0, 30, 80, 90, 100, 100}};

            std::vector<std::vector<double>> gammaWeightList = {
                {0, 0.05, 0.08, 1},
                {0.6, 0.6, 0.9, 0.9}};

            std::vector<std::vector<double>> sleepWeightList = {
                {0, 20, 40, 100},
                {0.5, 0.4, 0.3, 0.2}};

        }
    }
}

#endif /* AA86A1EC_6415_4A9B_A963_5DBEA640D102 */
