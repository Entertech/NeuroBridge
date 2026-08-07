#include "ModelAlgorithm.h"
#include "Basic.hpp"
#include "SleepParams.h"
namespace basic
{
    namespace affection
    {
        namespace model
        {
            nc::NdArray<double> rulerAdjust(const nc::NdArray<double> &ruler, double offset, double offsetRatio)
            {
                if (offsetRatio < 0)
                    throw std::invalid_argument("The value of ruler offset ratio should be greater or equal to zero!");

                if (offset * offsetRatio > 10)
                    throw std::invalid_argument("The value of ruler offset ratio may be too large, please try a smaller value!");

                auto adjRuler = ruler + offset * offsetRatio;
                return adjRuler;
            }

            void rulersCalibration(std::vector<nc::NdArray<double>> &featuresStore, const vectord &newFeatures,
                                   std::vector<nc::NdArray<double>> &curRulers,
                                   const std::vector<nc::NdArray<double>> &oriRulers, int adjScope,
                                   int adjPeriod, double offsetRatio)
            {
                // 初始化
                auto featureNum = featuresStore.size();

                for (size_t i = 0; i < featureNum; i++)
                {
                    if (int(featuresStore.at(i).size()) <= adjScope)
                    {

                        // 记录最初一段有效数据，用于修正标尺
                        nc::NdArray<double> nvalue = {newFeatures.at(i)};

                        featuresStore.at(i) = nc::hstack({featuresStore.at(i), nvalue});

                        // 多次修正标尺
                        if (featuresStore.at(i).size() % adjPeriod == 0)
                        {
                            auto offset = nc::median(featuresStore.at(i)).item() - nc::median(oriRulers.at(i)).item();
                            curRulers.at(i) = rulerAdjust(oriRulers.at(i), offset, offsetRatio);
                        }
                    }
                }
            }

            double rulerMap(double value, const nc::NdArray<double> &ruler, double stretchRatio, int mapMin, int mapMax)
            {
                auto res = double(mapMin);
                for (size_t i = 0; i < ruler.size(); i++)
                {
                    if (value >= ruler[i])
                        res = i + 1.;
                    else
                        break;
                }
                if (stretchRatio > 0)
                    res = nc::tanh((res - 50.) / 50.0 * stretchRatio) * 50.0 / nc::tanh(stretchRatio) + 50.;
                else if (stretchRatio < 0)
                    res = nc::arctanh((res - 50.) / 50.0 * nc::tanh(-stretchRatio)) * 50.0 / -stretchRatio + 50.;
                res = std::max(static_cast<double>(mapMin), std::min(static_cast<double>(mapMax), res));
                return res;
            }

            double outputProcess(double curValue, nc::NdArray<double> &valueStore, double weight, double initialVal, int initialScope)
            {
                curValue = weight * curValue + (1. - weight) * initialVal;
                double res = 0;
                nc::NdArray<double> curValueTmp = {curValue};
                if (int(valueStore.size()) < initialScope)
                {
                    valueStore = nc::hstack({valueStore, curValueTmp});
                    res = 0;
                }
                else
                {
                    valueStore = nc::hstack({valueStore[nc::Slice(1, valueStore.size())], curValueTmp});
                    res = nc::mean(valueStore).item();
                }
                return res;
            }

            /// @brief 注意力计算
            /// @param features 特征
            /// @param rulers 衡量注意力值的标尺
            /// @return 注意力值
            double attentionCal(vectord features, const std::vector<nc::NdArray<double>> &rulers)
            {
                // 初始化
                nc::NdArray<double> ratios = {1.9, 1.5, 1.2};
                nc::NdArray<double> weight = {0.4, 0.4, -0.2};
                nc::NdArray<double> bias = {0, 0, 20};
                vectord factors;
                auto featureNum = features.size();
                // 特征调节
                features.at(0) += 0.05;
                features.at(1) += 0.01;
                for (size_t i = 0; i < featureNum; i++)
                {
                    auto factor = rulerMap(features[i], rulers[i], ratios[i], 0, 100);

                    factors.push_back(double(factor));
                }
                nc::NdArray<double> ncFactors(factors, false);
                auto attention = nc::sum(nc::multiply(ncFactors, weight) + bias).item();
                attention = std::max(0.0, std::min(100.0, attention));

                return attention;
            }

            double relaxationCal(vectord features, const std::vector<nc::NdArray<double>> &rulers, bool singleChn = false)
            {
                // 初始化
                nc::NdArray<double> ratios = {1.9, 1.2, 2.8};
                nc::NdArray<double> weight = {0.2, -0.5, -0.3};
                nc::NdArray<double> bias = {0, 50, 30};
                if (singleChn)
                {
                    weight.at(0) = 0.35;
                    weight.at(1) = -0.65;
                    weight.at(2) = 0;
                    bias.at(0) = 0;
                    bias.at(1) = 65;
                    bias.at(2) = 0;
                }
                vectord factors;
                auto featureNum = features.size();
                // 特征调节
                features.at(0) += 0.02;
                for (size_t i = 0; i < featureNum; i++)
                {
                    auto factor = rulerMap(features[i], rulers[i], ratios[i], 0, 100);

                    factors.push_back(double(factor));
                }
                nc::NdArray<double> ncFactors(factors, false);
                auto relaxation = nc::sum(nc::multiply(ncFactors, weight) + bias).item();
                relaxation = std::max(0.0, std::min(100.0, relaxation));

                return relaxation;
            }

            /// @brief 冥想度计算
            /// @param features 特征
            /// @param rulers 衡量冥想度值的标尺
            /// @param ratios 标尺缩放比例
            /// @param weights 特征权重
            /// @param bias 特征偏置
            /// @return 冥想度值
            double meditationCal(vectord features, const std::vector<nc::NdArray<double>> &rulers, vectord ratios, vectord weights, vectord bias)
            {
                nc::NdArray<double> ratiosNc(ratios);
                nc::NdArray<double> weightNc(weights);
                nc::NdArray<double> biasNc(bias);
                vectord factors;
                auto featureNum = features.size();
                for (size_t i = 0; i < featureNum; i++)
                {
                    auto factor = rulerMap(features[i], rulers[i], ratiosNc[i], 0, 100);
                    factors.push_back(double(factor));
                }
                nc::NdArray<double> ncFactors(factors, false);
                auto meditation = nc::sum(nc::multiply(ncFactors, weightNc) + biasNc).item();
                meditation = std::max(0.0, std::min(100.0, meditation));

                return meditation;
            }

            double pressureCal(double hrVal, double hrvVal, double hrLf, double freqRate)
            {

                if (hrVal > 0 && hrvVal > 0 && hrLf > 0 && freqRate > 0)
                {
                    auto pressureHr = std::max(std::min((hrVal - 55.0) * 1.8, 100.0), 0.0);
                    auto pressureHrv = std::max(std::min(100 * nc::tanh(11.0 / hrvVal - 0.55) + 50.0, 120.0), -20.0);
                    auto pressureLf = std::max(std::min(110 * nc::tanh(2500.0 / hrLf - 0.55) + 50.0, 120.0), -20.0);
                    auto pressureFr = std::max(std::min(60 * nc::tanh((freqRate - 2.3) * 1.15) + 50.0, 100.0), 0.0);
                    auto pressure = pressureHr * 0.1 + pressureHrv * 0.4 + pressureLf * 0.37 + pressureFr * 0.13;
                    pressure = std::max(0.0, std::min(100.0, pressure));
                    return pressure;
                }
                else
                    return -1;
            }

            double pleasureCal(const vectord &features, const std::vector<nc::NdArray<double>> &rulers)
            {
                // 初始化
                nc::NdArray<double> ratios = {1.8, 1.2};
                nc::NdArray<double> weight = {0.6, 0.4};
                nc::NdArray<double> bias = {0, 0};
                vectord factors;
                auto featureNum = features.size();

                for (size_t i = 0; i < featureNum; i++)
                {
                    auto factor = rulerMap(features[i], rulers[i], ratios[i], 0, 100);
                    factors.push_back(factor);
                }
                nc::NdArray<double> ncFactors(factors, false);
                auto pleasure = nc::sum(nc::multiply(ncFactors, weight) + bias).item();
                pleasure = std::max(0.0, std::min(100.0, pleasure));
                return pleasure;
            }

            double arousalCal(double hrVal, double freqRate)
            {
                if (hrVal > 0 && freqRate > 0)
                {
                    auto arousalHrv = std::max(std::min(60.0 * nc::tanh((hrVal - 80.0) * 0.048) + 50.0, 120.0), -20.0);
                    auto arousalLf = std::max(std::min(55.0 * nc::tanh((freqRate - 2.5) * 1.15) + 50.0, 110.0), -10.0);
                    auto arousal = arousalHrv * 0.4 + arousalLf * 0.6;
                    arousal = std::max(0.0, std::min(100.0, arousal));
                    return arousal;
                }
                else
                    return -1;
            }

            double coherenceCal(double syncFreqCor)
            {
                if (syncFreqCor > 0)
                {
                    auto coherence = 100. * (std::pow(syncFreqCor, 2.0));
                    coherence = std::max(0.0, std::min(100.0, coherence));
                    return coherence;
                }
                else
                    return -1;
            }

            define::SleepPhaseEnum sleepPhaseCal(vectord modelRes)
            {
                auto maxElement = std::max_element(modelRes.begin(), modelRes.end());
                auto maxIndex = std::distance(modelRes.begin(), maxElement);
                if (maxIndex == 0) {
                    return define::SleepPhaseEnum::AWAKE;
                } else if (maxIndex == 2 || maxIndex == 3) {
                    return define::SleepPhaseEnum::ASLEEP;
                } else {
                    return define::SleepPhaseEnum::UNKNOWN;
                }
            }

            double sleepDegreeCal(double beta, double theta, double delta, double gamma, vectord modelRes, bool sleepFlag)
            {
                if (beta > 0 && theta > 0 && delta > 0 && gamma > 0)
                {
                    // 参数计算
                    auto betaDegreeList0 = params::betaDegreeList[0];
                    auto betaDegreeList1 = params::betaDegreeList[1];
                    auto betaFactor = mathtool::interp(beta, betaDegreeList0, betaDegreeList1);
                    auto betaWeight = mathtool::interp(beta, params::betaWeightList[0], params::betaWeightList[1]);
                    auto thetaFactor = mathtool::interp(theta, params::thetaDegreeList[0], params::thetaDegreeList.at(1));
                    auto thetaWeight = mathtool::interp(theta, params::thetaWeightList.at(0), params::thetaWeightList.at(1));
                    auto deltaFactor = mathtool::interp(delta, params::deltaDegreeList.at(0), params::deltaDegreeList.at(1));
                    auto deltaWeight = mathtool::interp(delta, params::deltaWeightList.at(0), params::deltaWeightList.at(1));
                    auto gammaFactor = mathtool::interp(gamma, params::gammaDegreeList.at(0), params::gammaDegreeList.at(1));
                    auto gammaWeight = mathtool::interp(gamma, params::gammaWeightList.at(0), params::gammaWeightList.at(1));

                    // 参数归一化
                    auto weightSum = betaWeight + thetaWeight + deltaWeight + gammaWeight;
                    betaWeight = betaWeight / weightSum;
                    thetaWeight = thetaWeight / weightSum;
                    deltaWeight = deltaWeight / weightSum;
                    gammaWeight = gammaWeight / weightSum;
                    auto sleepDepth = betaFactor * betaWeight + thetaFactor * thetaWeight + deltaFactor * deltaWeight + gammaFactor * gammaWeight;
                    auto modelBase = modelRes[0]*100+modelRes[1]*70+modelRes[2]*40+modelRes[4]*80;
                    double sleepPredict = 0;
                    if (sleepFlag)
                        sleepPredict = 0.95 * modelBase;
                    else
                        sleepPredict = 5 + 0.95 * modelBase;
                    auto depthWeight = mathtool::interp(sleepDepth, params::sleepWeightList.at(0), params::sleepWeightList.at(1));
                    auto sleepDegree = sleepDepth * depthWeight + sleepPredict * (1 - depthWeight);
                    return sleepDegree;
                }
                else
                    return -1;
            }

            /// 睡眠分期计算
            /// \param modelRes 模型输出
            /// \return 睡眠相位
            define::SleepStage sleepStageCal(vectord modelRes)
            {
                define::SleepStage sleepState = define::SleepStage::WAKE;

                auto maxElement = std::max_element(modelRes.begin(), modelRes.end());
                auto maxIndex = std::distance(modelRes.begin(), maxElement);
                switch (maxIndex) {
                    case 0:
                        sleepState = define::WAKE;
                        break;
                    case 1:
                        sleepState = define::NREM1;
                        break;
                    case 2:
                        sleepState = define::NREM2;
                        break;
                    case 3:
                        sleepState = define::NREM3;
                        break;
                    case 4:
                        sleepState = define::REM;
                        break;
                    default:
                        break;

                }
                return sleepState;

            }

            /// @brief 冥想程度计算
            /// @param modelRes 模型输出
            /// @return 冥想度
            double meditationDegreeCal(double res)
            {
                vectord coefficients = {45462.8925, -312062.435, 951885.2, -1701009.75,
                                        1976274.87, -1566185.93, 864098.531, -332787.539,
                                        88423.2732, -15798.947, 1824.79168, -130.11217,
                                        6.15258912, 0.00495365278
                };
                mathtool::Polynomial p(coefficients);
                auto modelBaseNormal = p(res);
//                auto polyValue = nc::polynomial::Poly1d<double>(polyList);
//                auto modelBaseNormal = polyValue(0.5);
                auto meditationDegree = (std::tan((modelBaseNormal - 0.65) / 0.65) + 1.557) / 2.155 * 100;
                return meditationDegree;
            }
        }
    }
}